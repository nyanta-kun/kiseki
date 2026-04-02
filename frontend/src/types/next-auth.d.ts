import type { DefaultSession } from "next-auth";
import type { DefaultJWT } from "next-auth/jwt";

declare module "next-auth" {
  interface Session {
    user: {
      role?: string;
      is_active?: boolean;
      is_premium?: boolean;
      access_expires_at?: string | null;
      db_id?: number;
    } & DefaultSession["user"];
  }
}

declare module "next-auth/jwt" {
  interface JWT extends DefaultJWT {
    role?: string;
    is_active?: boolean;
    is_premium?: boolean;
    access_expires_at?: string | null;
    db_id?: number;
  }
}
