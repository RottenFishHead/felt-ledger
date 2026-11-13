# Design System

## Colors

- Primary: #15563c (poker app — default)
- Primary (resell app): #1E3A8A
- Secondary: #e49708
- Page background: #F5F4F0 (warm gray behind everything)
- Background: #FFFFFF (cards, panels, inputs, header)
- Surface: #F8FAFC
- Text: #0F172A
- Muted text: #64748B
- Border: #E2E8F0
- Error: #DC2626
- Success: #16A34A
- Gain (money up): #1D4ED8
- Loss (money down): #C2410C

Use Gain/Loss (`.gain` / `.loss`) for profit and loss, never Success/Error:
blue vs orange stays readable for red-green colorblindness. Keep a +/− sign
or label next to the color too. Success/Error are for messages and form errors.

### Per-app primary

Each app sets its primary color via `data-app` on `<html>`
(`{% block app %}` in `templates/base.html`). Override only `--primary`
in `static/css/style.css` (`:root[data-app="<app>"]`); all other tokens
are shared. Resell templates extend `resell/base.html`.

## Spacing

Use a consistent spacing scale:

- 4px
- 8px
- 12px
- 16px
- 24px
- 32px
- 48px
- 64px
- 96px

## Border Radius

- Buttons: 8px
- Inputs: 8px
- Cards: 12px
- Large sections: 16px

Do not introduce random colors or spacing values
unless there is a good reason.

## Design Direction

Avoid generic AI-generated website patterns.

Do NOT automatically use:

- Purple/blue gradients
- Excessive glassmorphism
- Huge rounded cards
- Floating blobs
- Random glowing backgrounds
- Excessive drop shadows
- Every section inside a card
- Generic SaaS dashboards

Do not add visual effects just because they are
common in AI-generated websites.

Prefer:

- Strong typography
- Clear visual hierarchy
- Intentional whitespace
- Simple color palettes
- Distinctive layouts
- Real visual hierarchy

## Typography

Use the project's existing font whenever possible.

## Headings

- H1: 48-64px desktop
- H2: 36-48px desktop
- H3: 24-32px desktop

## Body

- Body: 16-18px
- Small text: 14px
- Line height: 1.5-1.7

## Typography Rules

- Do not use more than 2 font families.
- Keep heading weights between 600-800.
- Avoid extremely long paragraphs.
- Use comfortable line lengths.
- Use responsive typography on mobile.

Prefer CSS clamp() for large headings.

Example:
font-size: clamp(2.5rem, 5vw, 4rem);

## Layout Rules

## Container

Use:
max-width: 1200px;
Keep the main content centered.

## Horizontal Padding

Desktop:
24px
Tablet:
20px
Mobile:
16px

## Section Spacing

Desktop:
80px - 120px
Tablet:
64px - 80px
Mobile:
48px - 64px

## Rules

- Keep content aligned to the same container.
- Avoid unnecessary full-width content.
- Keep consistent spacing between sections.
- Do not use random margin values.
- Prefer the existing spacing scale.

## Responsive Design

Build mobile-first.

## Breakpoints

Mobile:
< 640px
Tablet:
640px - 1024px
Desktop:
> 1024px

## Mobile Rules

- Stack multi-column layouts.
- Reduce heading sizes.
- Reduce section spacing.
- Keep buttons easy to tap.
- Avoid horizontal scrolling.
- Keep navigation usable.
- Images should never overflow the viewport.

## Desktop Rules

- Use available horizontal space intelligently.
- Do not make content unnecessarily wide.
- Preserve readable text line lengths.

Always check the design at:

- 375px
- 768px
- 1024px
- 1440px
