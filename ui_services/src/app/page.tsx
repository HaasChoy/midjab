"use client";

import Link from "next/link";

export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-8 p-8">
      <h1 className="text-5xl font-bold tracking-tight">
        Mid<span className="text-indigo-400">Jab</span>
      </h1>
      <p className="max-w-md text-center text-lg text-gray-400">
        Upload your resume, discover matching jobs, and auto-tailor applications
        — all powered by AI.
      </p>

      <div className="flex gap-4">
        <Link
          href="/login"
          className="rounded-lg bg-indigo-600 px-6 py-3 font-semibold transition hover:bg-indigo-500"
        >
          Log in
        </Link>
        <Link
          href="/signup"
          className="rounded-lg border border-gray-700 px-6 py-3 font-semibold transition hover:border-gray-500"
        >
          Sign up
        </Link>
      </div>
    </main>
  );
}
