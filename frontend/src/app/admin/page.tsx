import { auth } from "@/auth";
import { redirect } from "next/navigation";
import { AdminTabs } from "./AdminTabs";
import type { User, InvitationCode } from "./AdminTabs";

const BACKEND_URL =
  process.env.BACKEND_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";
const API_KEY = process.env.INTERNAL_API_KEY ?? "";

async function fetchUsers(): Promise<User[]> {
  const res = await fetch(`${BACKEND_URL}/admin/users`, {
    headers: { "X-API-Key": API_KEY },
    cache: "no-store",
  });
  if (!res.ok) return [];
  return res.json() as Promise<User[]>;
}

async function fetchInvitationCodes(): Promise<InvitationCode[]> {
  const res = await fetch(`${BACKEND_URL}/admin/invitation-codes`, {
    headers: { "X-API-Key": API_KEY },
    cache: "no-store",
  });
  if (!res.ok) return [];
  return res.json() as Promise<InvitationCode[]>;
}

export default async function AdminPage() {
  const session = await auth();
  if (session?.user?.role !== "admin") redirect("/races");

  const [users, codes] = await Promise.all([fetchUsers(), fetchInvitationCodes()]);

  return (
    <div className="min-h-screen bg-[#f0f5fb]">
      <main className="p-6 max-w-6xl mx-auto">
        <AdminTabs users={users} codes={codes} />
      </main>
    </div>
  );
}
