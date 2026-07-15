import { useEffect, useState } from "react";
import { msalLogoutPopupStorageKey } from "../../lib/msalAuth";

export default function MsalCallback() {
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"sign-in" | "sign-out">("sign-in");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const hashParams = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    const hasAuthResponse =
      params.has("code") ||
      params.has("error") ||
      hashParams.has("code") ||
      hashParams.has("error");
    const isLogoutPopup =
      window.sessionStorage.getItem(msalLogoutPopupStorageKey) === "1";

    if (!hasAuthResponse) {
      window.sessionStorage.removeItem(msalLogoutPopupStorageKey);
      setMode("sign-out");
      window.close();
      return;
    }

    if (isLogoutPopup) {
      window.sessionStorage.removeItem(msalLogoutPopupStorageKey);
    }

    void import("@azure/msal-browser/redirect-bridge")
      .then(({ broadcastResponseToMainFrame }) => broadcastResponseToMainFrame())
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Unable to complete sign-in.");
      });
  }, []);

  return (
    <main className="flex min-h-screen items-center justify-center bg-white p-6 text-slate-950">
      <section className="max-w-sm rounded-lg border border-slate-200 p-6 text-center shadow-sm">
        <h1 className="text-lg font-semibold">
          {mode === "sign-out" ? "Completing sign-out" : "Completing sign-in"}
        </h1>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          {mode === "sign-out"
            ? "Sign-out is complete. You can close this popup."
            : "This popup should close automatically."}
        </p>
        {error ? (
          <p className="mt-3 rounded-md bg-red-50 p-3 text-xs leading-5 text-red-700">
            {error}
          </p>
        ) : null}
      </section>
    </main>
  );
}
