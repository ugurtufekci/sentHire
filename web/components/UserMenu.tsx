"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { Me } from "@/lib/types";

/** Topbar account area: workspace + user name with a small menu.
 *  Renders a quiet "Giriş" link when there is no session. */
export default function UserMenu() {
  const [me, setMe] = useState<Me | null | "anon">(null);
  const detailsRef = useRef<HTMLDetailsElement>(null);

  useEffect(() => {
    api
      .me()
      .then(setMe)
      .catch(() => setMe("anon"));
  }, []);

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (detailsRef.current?.open && !detailsRef.current.contains(e.target as Node)) {
        detailsRef.current.open = false;
      }
    };
    document.addEventListener("click", close);
    return () => document.removeEventListener("click", close);
  }, []);

  if (me === null) return <span className="tiny" aria-hidden="true" />;

  if (me === "anon") {
    return (
      <Link href="/login" className="btn btn-ghost">
        Giriş yap
      </Link>
    );
  }

  async function logout() {
    try {
      await api.logout();
    } finally {
      window.location.assign("/login");
    }
  }

  return (
    <details className="user-menu" ref={detailsRef}>
      <summary>
        <span className="user-org">{me.org.name}</span>
        <span className="user-name">{me.user.name || me.user.email}</span>
      </summary>
      <div className="menu-pop" onClick={() => (detailsRef.current!.open = false)}>
        <Link className="menu-item" href="/team">
          Ekip
        </Link>
        <Link className="menu-item" href="/billing">
          Plan ve kullanım
        </Link>
        <button className="menu-item" onClick={logout}>
          Çıkış yap
        </button>
      </div>
    </details>
  );
}
