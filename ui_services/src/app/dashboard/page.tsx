"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useSession, signOut } from "@/lib/auth-client";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

interface ResumeEntry {
  resume_id?: string;
  id?: string;
  name: string;
  content_json: Record<string, unknown>;
  message?: string;
}

export default function DashboardPage() {
  const router = useRouter();
  const { data: session, isPending } = useSession();

  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState<ResumeEntry | null>(null);
  const [error, setError] = useState("");

  const [resumes, setResumes] = useState<ResumeEntry[]>([]);
  const [pipelineMsg, setPipelineMsg] = useState("");

  // Redirect unauthenticated users
  useEffect(() => {
    if (!isPending && !session?.user) {
      router.push("/login");
    }
  }, [isPending, session, router]);

  // Fetch existing resumes on mount
  useEffect(() => {
    if (session?.user) {
      fetch(`${API_URL}/api/resume/list`, { credentials: "include" })
        .then((r) => r.json())
        .then(setResumes)
        .catch(() => {});
    }
  }, [session]);

  // ── Upload handler ──────────────────────────────────────────────────
  async function handleUpload(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;

    setError("");
    setUploadResult(null);
    setUploading(true);

    try {
      const formData = new FormData();
      formData.append("file", file);

      const res = await fetch(`${API_URL}/api/resume/upload-pdf`, {
        method: "POST",
        body: formData,
        credentials: "include", // send Better Auth session cookie
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `Upload failed (${res.status})`);
      }

      const data: ResumeEntry = await res.json();
      setUploadResult(data);

      // Refresh list
      fetch(`${API_URL}/api/resume/list`, { credentials: "include" })
        .then((r) => r.json())
        .then(setResumes)
        .catch(() => {});
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  // ── Pipeline trigger ────────────────────────────────────────────────
  async function runPipeline(resumeId: string) {
    setPipelineMsg("");
    try {
      const res = await fetch(`${API_URL}/api/pipeline/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resume_id: resumeId }),
        credentials: "include",
      });
      const data = await res.json();
      setPipelineMsg(data.message ?? "Pipeline finished");
    } catch {
      setPipelineMsg("Pipeline failed");
    }
  }

  // ── Loading state ───────────────────────────────────────────────────
  if (isPending) {
    return (
      <main className="flex min-h-screen items-center justify-center">
        <p className="text-gray-400">Loading…</p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-3xl space-y-10 p-8">
      {/* ── Header ─────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Dashboard</h1>
        <div className="flex items-center gap-4">
          <span className="text-sm text-gray-400">
            {session?.user?.name ?? session?.user?.email}
          </span>
          <button
            onClick={() => signOut().then(() => router.push("/login"))}
            className="rounded border border-gray-700 px-3 py-1.5 text-sm transition hover:border-gray-500"
          >
            Sign out
          </button>
        </div>
      </div>

      {/* ── Upload section ─────────────────────────────────────────── */}
      <section className="rounded-xl border border-gray-800 bg-gray-900 p-6">
        <h2 className="mb-4 text-lg font-semibold">Upload Resume (PDF)</h2>
        <form onSubmit={handleUpload} className="flex items-end gap-4">
          <div className="flex-1">
            <input
              type="file"
              accept=".pdf"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="block w-full text-sm text-gray-400 file:mr-4 file:rounded-lg file:border-0 file:bg-indigo-600 file:px-4 file:py-2 file:text-sm file:font-semibold file:text-white hover:file:bg-indigo-500"
            />
          </div>
          <button
            type="submit"
            disabled={!file || uploading}
            className="rounded-lg bg-indigo-600 px-5 py-2.5 font-semibold transition hover:bg-indigo-500 disabled:opacity-50"
          >
            {uploading ? "Uploading…" : "Upload & Parse"}
          </button>
        </form>

        {error && (
          <p className="mt-3 text-sm text-red-400">{error}</p>
        )}

        {uploadResult && (
          <div className="mt-4 rounded-lg bg-green-900/30 p-4 text-sm text-green-300">
            ✅ {uploadResult.message} — <strong>{uploadResult.name}</strong>
          </div>
        )}
      </section>

      {/* ── Resumes list ───────────────────────────────────────────── */}
      <section className="rounded-xl border border-gray-800 bg-gray-900 p-6">
        <h2 className="mb-4 text-lg font-semibold">Your Resumes</h2>

        {resumes.length === 0 ? (
          <p className="text-gray-500">No resumes yet. Upload one above!</p>
        ) : (
          <ul className="space-y-3">
            {resumes.map((r) => {
              const rid = r.resume_id ?? r.id ?? "";
              return (
                <li
                  key={rid}
                  className="flex items-center justify-between rounded-lg border border-gray-800 p-4"
                >
                  <div>
                    <p className="font-medium">{r.name}</p>
                    <p className="text-xs text-gray-500">{rid}</p>
                  </div>
                  <button
                    onClick={() => runPipeline(rid)}
                    className="rounded bg-emerald-700 px-4 py-1.5 text-sm font-medium transition hover:bg-emerald-600"
                  >
                    Run Pipeline
                  </button>
                </li>
              );
            })}
          </ul>
        )}

        {pipelineMsg && (
          <p className="mt-4 text-sm text-indigo-300">{pipelineMsg}</p>
        )}
      </section>
    </main>
  );
}
