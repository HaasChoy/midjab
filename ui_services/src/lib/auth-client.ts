/**
 * Better Auth — Client-side helper for React components.
 */
import { createAuthClient } from "better-auth/react";

export const authClient = createAuthClient({
  baseURL: process.env.NEXT_PUBLIC_BETTER_AUTH_URL ?? "http://localhost:3000",
});

// Convenience re-exports
export const { signIn, signUp, signOut, useSession } = authClient;
