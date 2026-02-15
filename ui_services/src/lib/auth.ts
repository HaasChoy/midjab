/**
 * Better Auth — Server-side configuration.
 *
 * Uses the SAME PostgreSQL database as brain_midjab.
 * Maps to the existing `users` table with snake_case columns.
 */
import { betterAuth } from "better-auth";

export const auth = betterAuth({
  database: {
    type: "postgres",
    url: process.env.DATABASE_URL!,
  },

  // ── Email + Password auth ───────────────────────────────────────────
  emailAndPassword: {
    enabled: true,
    minPasswordLength: 8,
  },

  // ── Map to existing `users` table (snake_case columns) ─────────────
  user: {
    modelName: "users",
    fields: {
      emailVerified: "email_verified",
      createdAt: "created_at",
      updatedAt: "updated_at",
    },
  },

  session: {
    modelName: "sessions",
    fields: {
      expiresAt: "expires_at",
      createdAt: "created_at",
      updatedAt: "updated_at",
      userId: "user_id",
      ipAddress: "ip_address",
      userAgent: "user_agent",
    },
  },

  account: {
    modelName: "accounts",
    fields: {
      accountId: "account_id",
      providerId: "provider_id",
      userId: "user_id",
      accessToken: "access_token",
      refreshToken: "refresh_token",
      idToken: "id_token",
      accessTokenExpiresAt: "access_token_expires_at",
      refreshTokenExpiresAt: "refresh_token_expires_at",
      createdAt: "created_at",
      updatedAt: "updated_at",
    },
  },

  verification: {
    modelName: "verifications",
    fields: {
      expiresAt: "expires_at",
      createdAt: "created_at",
      updatedAt: "updated_at",
    },
  },
});
