import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const PUBLIC_PATHS = ["/", "/login"];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  if (PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`))) {
    return NextResponse.next();
  }
  // Client-side token check — server middleware cannot read localStorage.
  // Protected routes rely on useAuth() redirect in page components.
  return NextResponse.next();
}

export const config = {
  matcher: ["/session/:path*"],
};
