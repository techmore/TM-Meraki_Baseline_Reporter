def index_css(max_width: int = 1180) -> str:
    return """    :root { color-scheme: light; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #172033; background: #f7f8fb; }
    main { max-width: __MAX_WIDTH__px; margin: 0 auto; }
    header { margin-bottom: 24px; }
    h1 { margin: 0 0 6px; font-size: 28px; }
    h2 { margin: 0 0 4px; font-size: 20px; }
    p { margin: 0 0 14px; color: #526071; }
    section { background: #fff; border: 1px solid #d9dee8; border-radius: 8px; padding: 18px; margin: 16px 0; overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; font-size: 14px; }
    th, td { border-bottom: 1px solid #e7ebf2; padding: 8px 10px; text-align: left; vertical-align: top; }
    th { color: #526071; font-size: 12px; text-transform: uppercase; letter-spacing: .02em; }
    a { color: #185abc; text-decoration: none; }
    a:hover { text-decoration: underline; }
    .meta { display: flex; gap: 14px; flex-wrap: wrap; font-size: 14px; color: #526071; }
    .status { display: inline-block; border-radius: 999px; padding: 2px 8px; font-size: 12px; font-weight: 700; }
    .ok { background: #e7f5ec; color: #176a35; }
    .warn, .optional { background: #fff7db; color: #755600; }
    .bad, .missing { background: #fde8e8; color: #a62121; }""".replace("__MAX_WIDTH__", str(max_width))
