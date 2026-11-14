import type { ReactNode } from "react";

type IconName =
  | "document"
  | "chat"
  | "phone"
  | "phone-off"
  | "check"
  | "spark"
  | "dots"
  | "arrow-up-right"
  | "arrow-right"
  | "loop"
  | "database"
  | "shield"
  | "list"
  | "command"
  | "users"
  | "lock"
  | "sparkle"
  | "logout"
  | "overview"
  | "search";

const PATHS: Record<IconName, ReactNode> = {
  document: (
    <>
      <path d="M6 3.5h7.5L18 8v12.5H6z" />
      <path d="M13.5 3.5V8H18" />
      <path d="M8.8 12h6.4M8.8 15.5h6.4" />
    </>
  ),
  chat: (
    <>
      <path d="M4.5 6.5A2.5 2.5 0 0 1 7 4h10a2.5 2.5 0 0 1 2.5 2.5v6A2.5 2.5 0 0 1 17 15H9l-4.5 4z" />
      <path d="M8 9h8M8 12h5" />
    </>
  ),
  phone: (
    <path d="M6.2 3.8 9 6.6 7.4 9a13.4 13.4 0 0 0 7.6 7.6l2.4-1.6 2.8 2.8c-.7 1-1.9 1.6-3.2 1.4A17.5 17.5 0 0 1 4.8 7.2c-.2-1.3.4-2.5 1.4-3.4Z" />
  ),
  "phone-off": (
    <>
      <path d="M6.2 3.8 9 6.6 7.4 9a13.4 13.4 0 0 0 7.6 7.6l2.4-1.6 2.8 2.8c-.7 1-1.9 1.6-3.2 1.4A17.5 17.5 0 0 1 4.8 7.2c-.2-1.3.4-2.5 1.4-3.4Z" />
      <path d="m4 4 16 16" />
    </>
  ),
  check: <path d="m5 12.5 4.2 4.2L19 6.8" />,
  spark: (
    <>
      <path d="M12 3.5 13.6 9l5.4 1.6-5.4 1.6L12 17.5l-1.6-5.3L5 10.6 10.4 9z" />
      <path d="M18.5 16.5v3M17 18h3" />
    </>
  ),
  dots: (
    <>
      <circle cx="6" cy="12" r="1.2" fill="currentColor" stroke="none" />
      <circle cx="12" cy="12" r="1.2" fill="currentColor" stroke="none" />
      <circle cx="18" cy="12" r="1.2" fill="currentColor" stroke="none" />
    </>
  ),
  "arrow-up-right": <path d="M7 17 17 7M8.5 7H17v8.5" />,
  "arrow-right": <path d="M4 12h15M13 6l6 6-6 6" />,
  loop: (
    <>
      <path d="M20 11.5a8 8 0 1 1-2.3-5.1" />
      <path d="M20 4v5h-5" />
    </>
  ),
  database: (
    <>
      <ellipse cx="12" cy="6" rx="7.5" ry="3" />
      <path d="M4.5 6v12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3V6" />
      <path d="M4.5 12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3" />
    </>
  ),
  shield: (
    <>
      <path d="M12 3.5 5 6v5c0 4.5 3 7.8 7 9.5 4-1.7 7-5 7-9.5V6z" />
      <path d="m9 12 2.2 2.2 4-4.4" />
    </>
  ),
  list: (
    <>
      <path d="M8 6.5h11M8 12h11M8 17.5h11" />
      <circle cx="4" cy="6.5" r="1.1" fill="currentColor" stroke="none" />
      <circle cx="4" cy="12" r="1.1" fill="currentColor" stroke="none" />
      <circle cx="4" cy="17.5" r="1.1" fill="currentColor" stroke="none" />
    </>
  ),
  command: (
    <path d="M9 9V6.5A2.5 2.5 0 1 0 6.5 9H9zm0 0h6m-6 0v6m6-6V6.5A2.5 2.5 0 1 1 17.5 9H15zm0 6H9m6 0v2.5A2.5 2.5 0 1 0 17.5 15H15zm-6 0v2.5A2.5 2.5 0 1 1 6.5 15H9z" />
  ),
  users: (
    <>
      <circle cx="9" cy="8" r="3.2" />
      <path d="M3.5 20c0-3.2 2.5-5.4 5.5-5.4s5.5 2.2 5.5 5.4" />
      <path d="M16 5.4a3.2 3.2 0 0 1 0 6.2M17.4 14.9c2 .5 3.3 2.2 3.3 4.4" />
    </>
  ),
  lock: (
    <>
      <rect x="5" y="10.5" width="14" height="9.5" rx="2.4" />
      <path d="M8 10.5V8a4 4 0 0 1 8 0v2.5" />
      <circle cx="12" cy="15" r="1.2" fill="currentColor" stroke="none" />
    </>
  ),
  sparkle: (
    <>
      <path d="M12 4l1.7 4.6L18.5 10l-4.8 1.4L12 16l-1.7-4.6L5.5 10l4.8-1.4z" />
      <path d="M18 15.5l.7 1.9 1.8.6-1.8.6-.7 1.9-.7-1.9-1.8-.6 1.8-.6z" />
    </>
  ),
  logout: (
    <>
      <path d="M14 4h3.5A2.5 2.5 0 0 1 20 6.5v11a2.5 2.5 0 0 1-2.5 2.5H14" />
      <path d="M10 8 6 12l4 4M6 12h10" />
    </>
  ),
  overview: (
    <>
      <rect x="3.5" y="3.5" width="7" height="7" rx="1.8" />
      <rect x="13.5" y="3.5" width="7" height="7" rx="1.8" />
      <rect x="3.5" y="13.5" width="7" height="7" rx="1.8" />
      <rect x="13.5" y="13.5" width="7" height="7" rx="1.8" />
    </>
  ),
  search: (
    <>
      <circle cx="10.8" cy="10.8" r="6.3" />
      <path d="m15.5 15.5 4 4" />
    </>
  ),
};

export function Icon({ name, className }: { name: IconName; className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {PATHS[name]}
    </svg>
  );
}

export type { IconName };
