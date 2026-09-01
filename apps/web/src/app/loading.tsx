import { Logo } from "@/components/logo";

export default function Loading() {
  return (
    <main className="session-checking" aria-live="polite">
      <Logo />
      <i />
      <span>正在加载工作台…</span>
    </main>
  );
}
