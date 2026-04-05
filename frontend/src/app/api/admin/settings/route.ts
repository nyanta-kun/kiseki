import { auth } from "@/auth";
import { NextResponse, type NextRequest } from "next/server";

const BACKEND_URL =
  process.env.BACKEND_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";
const API_KEY = process.env.INTERNAL_API_KEY ?? "";

export async function GET() {
  const session = await auth();
  if (session?.user?.role !== "admin") {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }
  const res = await fetch(`${BACKEND_URL}/admin/settings`, {
    headers: { "X-API-Key": API_KEY },
    cache: "no-store",
  });
  if (!res.ok) {
    return NextResponse.json({ error: "Backend error" }, { status: 502 });
  }
  return NextResponse.json(await res.json());
}

export async function PUT(req: NextRequest) {
  const session = await auth();
  if (session?.user?.role !== "admin") {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }
  const body = await req.json();
  const res = await fetch(`${BACKEND_URL}/admin/settings`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": API_KEY,
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    return NextResponse.json({ error: "Backend error" }, { status: 502 });
  }
  return NextResponse.json(await res.json());
}
