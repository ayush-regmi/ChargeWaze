export function SignOutButton() {
  return (
    <form action="/auth/sign-out" method="post" className="inline">
      <button
        type="submit"
        className="rounded-md border border-border bg-background px-4 py-2 text-sm font-medium hover:bg-accent"
      >
        Sign out
      </button>
    </form>
  );
}
