/**
 * Better Auth catch-all API route.
 *
 * All auth requests (signup, login, session, etc.) are handled here.
 * Better Auth auto-discovers endpoints under /api/auth/*.
 */
import { auth } from "@/lib/auth";
import { toNextJsHandler } from "better-auth/next-js";

export const { GET, POST } = toNextJsHandler(auth.handler);
