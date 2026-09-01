import Link from "next/link";

import { Logo } from "@/components/logo";

export default function NotFound() {
  return (
    <main className="route-fallback">
      <Logo />
      <h1>页面不存在</h1>
      <p>请求的地址没有匹配的工作台页面。</p>
      <Link className="primary-button" href="/">
        返回工作台
      </Link>
    </main>
  );
}
