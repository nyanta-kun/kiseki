import { auth } from "@/auth";
import { NextResponse } from "next/server";

const BACKEND_URL =
  process.env.BACKEND_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";
const API_KEY = process.env.INTERNAL_API_KEY ?? "";

export async function GET() {
  const session = await auth();
  if (session?.user?.role !== "admin") {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }

  const res = await fetch(`${BACKEND_URL}/admin/data-coverage`, {
    headers: { "X-API-Key": API_KEY },
    cache: "no-store",
  });

  if (!res.ok) {
    return NextResponse.json({ error: "Backend error" }, { status: 502 });
  }

  return NextResponse.json(await res.json());
}
