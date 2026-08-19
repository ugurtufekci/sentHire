"""Corpus CLI — build and label an evaluation set without reading CVs one by one.

    # 1. bring CVs in (de-identified at the door; originals stay out of the repo)
    python -m senthire.evals.corpus_cli import --pool satis-2026 ~/cvs/*.pdf
    python -m senthire.evals.corpus_cli import --pool satis-2026 --profiles dump/*.json

    # 2. attach a job, then label everything the rules can answer for free and
    #    everything else with an oracle ensemble
    python -m senthire.evals.corpus_cli attach-job --pool satis-2026 --job b2b --spec spec.json
    python -m senthire.evals.corpus_cli label --pool satis-2026 --job b2b

    # 3. only the disagreements need a person (usually a handful)
    python -m senthire.evals.corpus_cli review --pool satis-2026 --job b2b
    python -m senthire.evals.corpus_cli adjudicate --pool satis-2026 --job b2b decisions.json

    # 4. promote to a golden case; CI runs it from then on
    python -m senthire.evals.corpus_cli promote --pool satis-2026 --job b2b --name b2b-2026

`--salt` (or SENTHIRE_CORPUS_SALT) keys the pseudonyms. Keep it out of the
repository: without it nobody can map a corpus case back to a person, and with
it the same person always maps to the same pseudonym.
"""

import argparse
import hashlib
import os
import sys
from datetime import date
from pathlib import Path

from senthire.domain.spec import EvaluationSpec
from senthire.evals import autolabel
from senthire.evals.corpus import LabelSet, Pool, make_case, read_json, write_json
from senthire.evals.promote import promote

DEFAULT_ROOT = Path("corpus")
DEFAULT_GOLDEN_ROOT = Path("goldens/cases")


def _salt(args) -> str:
    salt = args.salt or os.environ.get("SENTHIRE_CORPUS_SALT")
    if not salt:
        raise SystemExit(
            "a salt is required: pass --salt or set SENTHIRE_CORPUS_SALT. It keys the "
            "pseudonyms, so keep it outside the repository."
        )
    return salt


def _pool(args) -> Pool:
    return Pool(args.root, args.pool)


def cmd_import(args) -> int:
    salt, pool = _salt(args), _pool(args)
    today = args.as_of or date.today()
    added = skipped = failed = 0

    for path in args.paths:
        try:
            if args.profiles:
                payload = read_json(path)
                profile = payload.get("profile", payload)
                text = payload.get("text")
                seed = payload.get("sha256") or hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                source = {"kind": "profile-json", "filename": path.name}
            else:
                from senthire.extraction.extractor import extract_pdf

                data = path.read_bytes()
                seed = hashlib.sha256(data).hexdigest()
                outcome = extract_pdf(data)
                profile = outcome.profile.model_dump(mode="json")
                text = outcome.raw_text
                source = {
                    "kind": "pdf",
                    "model": outcome.model,
                    "prompt_version": outcome.prompt_version,
                    "path": outcome.path,
                    "pages": outcome.page_count,
                }
            case = make_case(
                profile, salt=salt, seed=seed, text=text, imported_at=today,
                source=source, tags=args.tag or [],
            )
        except Exception as exc:  # one malformed CV must not end the import
            print(f"  ! {path.name}: {type(exc).__name__}: {exc}")
            failed += 1
            continue
        if pool.add(case):
            added += 1
            print(f"  + {case.corpus_id}  {path.name}")
        else:
            skipped += 1
            print(f"  = {path.name} (duplicate profile, already in the pool)")

    print(f"\nimported {added}, duplicates {skipped}, failed {failed} → {pool.cases_dir}")
    return 1 if failed and not added else 0


def cmd_attach_job(args) -> int:
    pool = _pool(args)
    spec = EvaluationSpec.model_validate(read_json(args.spec))
    pool.save_spec(args.job, spec)
    print(f"spec v{spec.version} attached to {pool.name}/{args.job}")
    return 0


def cmd_label(args) -> int:
    pool = _pool(args)
    spec = pool.spec(args.job)
    as_of = args.as_of or date.today()
    oracle = _resolve_oracle(args)
    existing = pool.labels(args.job)
    cases = pool.cases()

    label_set = existing or LabelSet(
        pool=pool.name, job=args.job, spec_version=spec.version, labeled_at=as_of,
    )
    label_set.oracle = {
        "model": "fake" if args.fake_oracle else (args.oracle_model or _oracle_model()),
        "lenses": list(autolabel.LENSES),
        "min_agreement": args.min_agreement,
    }
    report = autolabel.LabelingReport()
    queue: list[dict] = []

    for case in cases:
        if case.corpus_id in label_set.cases and not args.relabel:
            continue
        labels = autolabel.label_case(
            spec, case, oracle=oracle, as_of=as_of,
            min_agreement=args.min_agreement, report=report,
        )
        label_set.cases[case.corpus_id] = labels
        queue.extend(autolabel.adjudication_items(spec, case, labels))
        print(
            f"  {case.corpus_id}  "
            + " ".join(
                f"{req}={lbl.verdict}{'?' if lbl.needs_adjudication else ''}"
                for req, lbl in sorted(labels.items())
            )
        )

    label_set.labeled_at = as_of
    pool.save_labels(label_set)
    pool.save_adjudication(args.job, queue)
    total = report.deterministic + report.labeled
    human = len(queue)
    print(
        f"\n{len(label_set.cases)} cases · {total} labels "
        f"({report.deterministic} free from rules, {report.labeled} by ensemble)\n"
        f"{report.unanimous} agreed, {human} need a human "
        f"({human / total:.1%} of labels), {report.dropped_evidence} votes dropped "
        "for unverifiable quotes"
    )
    return 0


def _oracle_model() -> str:
    from senthire.config import get_settings

    return get_settings().label_oracle_model


def _resolve_oracle(args):
    if args.fake_oracle:
        from senthire.evals.fake_oracle import deterministic_oracle

        return deterministic_oracle
    if args.oracle_model:
        os.environ["SENTHIRE_LABEL_ORACLE_MODEL"] = args.oracle_model
        from senthire.config import get_settings

        get_settings.cache_clear()
    return autolabel.model_oracle


def cmd_review(args) -> int:
    pool = _pool(args)
    queue = pool.adjudication(args.job)
    if not queue:
        print("nothing to adjudicate — every label was agreed")
        return 0
    print(f"{len(queue)} disagreement(s). Decide the verdict; you are not reading CVs.\n")
    for item in queue:
        print(f"- {item['corpus_id']} / {item['req_id']} — {item['requirement']}")
        print(f"    votes: {item['votes']}  leading: {item['leading_verdict']}")
        if item.get("rationale"):
            print(f"    rationale: {item['rationale']}")
    template = [
        {"corpus_id": i["corpus_id"], "req_id": i["req_id"], "verdict": i["leading_verdict"]}
        for i in queue
    ]
    if args.write_template:
        write_json(args.write_template, {"decisions": template})
        print(f"\ndecision template → {args.write_template} (edit 'verdict', then adjudicate)")
    return 0


def cmd_adjudicate(args) -> int:
    pool = _pool(args)
    label_set = pool.labels(args.job)
    if label_set is None:
        raise SystemExit("no labels yet")
    decisions = read_json(args.decisions)["decisions"]
    applied = autolabel.apply_adjudications(label_set.cases, decisions)
    pool.save_labels(label_set)
    remaining = label_set.unresolved()
    pool.save_adjudication(args.job, [])
    print(f"applied {applied} decision(s); {len(remaining)} still unresolved")
    return 0


def cmd_promote(args) -> int:
    pool = _pool(args)
    order_pairs = read_json(args.order_pairs)["pairs"] if args.order_pairs else None
    report = promote(
        pool,
        args.job,
        out_root=args.golden_root,
        case_name=args.name,
        salt=_salt(args),
        as_of=args.as_of or date.today(),
        min_confidence=args.min_confidence,
        top_k=args.top_k,
        pin_scores=args.pin_scores,
        with_invariants=not args.no_invariants,
        order_pairs=order_pairs,
    )
    if not report.promoted:
        print("nothing promoted — every case was skipped:")
        for reason, ids in sorted(report.skipped.items()):
            print(f"  {reason}: {len(ids)}")
        return 1
    print(f"promoted {len(report.promoted)} case(s) → {report.case_dir}")
    if report.twins:
        print("  invariant twins: " + ", ".join(f"{k}×{v}" for k, v in sorted(report.twins.items())))
    for reason, ids in sorted(report.skipped.items()):
        print(f"  skipped ({reason}): {len(ids)}")
    for rejected in report.rejected_twins:
        print(f"  ! dropped twin — {rejected}")
    print("\nrun `python -m senthire.evals` to check the new case")
    return 0


def cmd_stats(args) -> int:
    pool = _pool(args)
    cases = pool.cases()
    classes: dict[str, int] = {}
    for case in cases:
        classes[case.identity_class] = classes.get(case.identity_class, 0) + 1
    print(f"pool {pool.name}: {len(cases)} case(s)")
    print("  name coding: " + (", ".join(f"{k}={v}" for k, v in sorted(classes.items())) or "—"))
    for job in pool.jobs():
        label_set = pool.labels(job)
        if label_set is None:
            print(f"  job {job}: no labels")
            continue
        labels = [lbl for reqs in label_set.cases.values() for lbl in reqs.values()]
        by_source: dict[str, int] = {}
        for label in labels:
            by_source[label.source] = by_source.get(label.source, 0) + 1
        print(
            f"  job {job}: {len(label_set.cases)} labeled case(s), {len(labels)} label(s) "
            f"({', '.join(f'{k}={v}' for k, v in sorted(by_source.items()))}), "
            f"{len(label_set.unresolved())} awaiting a human"
        )
    return 0


def _add_common(parser) -> None:
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--pool", required=True)
    parser.add_argument("--salt", default=None)
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m senthire.evals.corpus_cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("import", help="de-identify CVs into a pool")
    _add_common(p)
    p.add_argument("paths", nargs="+", type=Path)
    p.add_argument("--profiles", action="store_true", help="inputs are extracted JSON, not PDFs")
    p.add_argument("--tag", action="append", default=[])
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("attach-job", help="attach an EvaluationSpec to label against")
    _add_common(p)
    p.add_argument("--job", required=True)
    p.add_argument("--spec", required=True, type=Path)
    p.set_defaults(func=cmd_attach_job)

    p = sub.add_parser("label", help="label the pool (rules for free, ensemble for the rest)")
    _add_common(p)
    p.add_argument("--job", required=True)
    p.add_argument("--min-agreement", type=float, default=1.0)
    p.add_argument("--oracle-model", default=None)
    p.add_argument("--fake-oracle", action="store_true", help="offline smoke test, no API calls")
    p.add_argument("--relabel", action="store_true", help="re-label cases that already have labels")
    p.set_defaults(func=cmd_label)

    p = sub.add_parser("review", help="show the disagreements a human still owns")
    _add_common(p)
    p.add_argument("--job", required=True)
    p.add_argument("--write-template", type=Path, default=None)
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("adjudicate", help="apply human decisions to the labels")
    _add_common(p)
    p.add_argument("--job", required=True)
    p.add_argument("decisions", type=Path)
    p.set_defaults(func=cmd_adjudicate)

    p = sub.add_parser("promote", help="write a golden case from labels + invariants")
    _add_common(p)
    p.add_argument("--job", required=True)
    p.add_argument("--name", required=True, help="golden case directory name")
    p.add_argument("--golden-root", type=Path, default=DEFAULT_GOLDEN_ROOT)
    p.add_argument("--min-confidence", type=float, default=0.8)
    p.add_argument("--top-k", type=int, default=3)
    p.add_argument("--pin-scores", action="store_true", help="also pin band and exact score")
    p.add_argument("--no-invariants", action="store_true")
    p.add_argument("--order-pairs", type=Path, default=None, help="pairwise ranking labels")
    p.set_defaults(func=cmd_promote)

    p = sub.add_parser("stats", help="what the pool contains")
    _add_common(p)
    p.set_defaults(func=cmd_stats)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
