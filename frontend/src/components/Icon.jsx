const paths = {
  route: (
    <>
      <circle cx="5" cy="6" r="2.2" />
      <circle cx="19" cy="6" r="2.2" />
      <circle cx="12" cy="18" r="2.2" />
      <path d="M7.2 6h9.6M6.2 8l4.6 8M17.8 8l-4.6 8" />
    </>
  ),
  check: <path d="M20 6 9 17l-5-5" />,
  x: <path d="M18 6 6 18M6 6l12 12" />,
  help: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M9.6 9a2.5 2.5 0 1 1 4.3 1.7c-.9.8-1.9 1.2-1.9 2.6" />
      <path d="M12 17.2h.01" />
    </>
  ),
  arrowUp: <path d="M12 19V5M5 12l7-7 7 7" />,
  arrowRight: <path d="M5 12h14M13 6l6 6-6 6" />,
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  coin: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M15 9.5c-.6-.8-1.6-1.2-3-1.2-1.7 0-2.8.9-2.8 2.1 0 1.3 1.1 1.8 2.9 2.2 2 .4 3 .9 3 2.2 0 1.3-1.2 2.2-3.1 2.2-1.4 0-2.5-.4-3.2-1.3" />
      <path d="M12 6v12" />
    </>
  ),
  gauge: (
    <>
      <path d="M4 15a8 8 0 1 1 16 0" />
      <path d="M12 15l3.5-4" />
      <circle cx="12" cy="15" r="1.4" />
    </>
  ),
  link: (
    <>
      <path d="M10 13a5 5 0 0 0 7.1 0l1.4-1.4a5 5 0 0 0-7.1-7.1L10 5.9" />
      <path d="M14 11a5 5 0 0 0-7.1 0l-1.4 1.4a5 5 0 0 0 7.1 7.1L14 18.1" />
    </>
  ),
  spark: (
    <>
      <path d="m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3Z" />
      <path d="m18.5 16 .8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8.8-2.2Z" />
    </>
  ),
  alert: (
    <>
      <path d="M12 4 3 20h18L12 4Z" />
      <path d="M12 9v5M12 17h.01" />
    </>
  ),
  chevron: <path d="m9 6 6 6-6 6" />,
  search: (
    <>
      <circle cx="11" cy="11" r="6" />
      <path d="m16 16 5 5" />
    </>
  ),
  shield: (
    <>
      <path d="M12 3 19 6v6c0 4.3-3 7.4-7 9-4-1.6-7-4.7-7-9V6l7-3Z" />
      <path d="m9 12 2 2 4-4" />
    </>
  ),
  layers: (
    <>
      <path d="m12 3 9 5-9 5-9-5 9-5Z" />
      <path d="m3 13 9 5 9-5" />
    </>
  ),
};

export function Icon({ name, size = 16, className = "", strokeWidth = 1.8 }) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {paths[name] || paths.spark}
    </svg>
  );
}
