export function SignOutButton() {
  return (
    <form action="/auth/sign-out" method="post" className="inline">
      <button
        type="submit"
        className="rounded-md border border-neutral-300 bg-white px-4 py-2 text-sm font-medium hover:bg-neutral-50 dark:border-neutral-700 dark:bg-neutral-950 dark:hover:bg-neutral-900"
      >
        Sign out
      </button>
    </form>
  );
}
