import { redirect } from "next/navigation";

/**
 * Root route. Until auth lands in Slice 2, this redirects straight to the
 * Dashboard so the app shell is reachable. Slice 2 will redirect
 * unauthenticated users to `/sign-in` via middleware instead.
 */
export default function Home(): never {
  redirect("/dashboard");
}
