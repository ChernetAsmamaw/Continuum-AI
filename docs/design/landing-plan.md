# Landing page plan: "Watch it, then try it"

Replaces the text-heavy "30-day diary" version (619 words, no images or video).

## What the research says (directional; mostly vendor data)
- A working product on the page beats a picture of it. One case: 43% of visitors engaged with an embedded interactive demo vs 8% with video. ~18% of B2B SaaS sites now embed one. ([Userpilot](https://userpilot.com/blog/interactive-product-demo/), [Winsome Marketing](https://www.winsomemarketing.com/resources/saas-marketing-interactive-demo-platforms-with-ai-customization))
- Strong product sites show looping or animated real UI instead of static screenshots (Linear, Vercel); Stripe leads with motion plus proof. ([Linear breakdown](https://webtrends.pages.dev/posts/linear-landing-page-breakdown), [Vercel on SaaSFrame](https://www.saasframe.io/examples/vercel-landing-page))
- Video testimonials convert best ([SaaS Hero](https://www.saashero.net/strategy/high-converting-landing-page-examples/)). We have none and won't invent any.

## Structure
| # | Section | On screen | Text |
|---|---|---|---|
| 1 | Hero | Audience line, short headline, "Try it here ↓" + "Open full demo". Looping silent video recorded from the prototype (queue → verify → next → call). Pause control; poster when reduced motion. | ~30 words |
| 2 | Play the 30 days | Drag a day slider 0–30 or press Play. Six follow-ups close, go red, get chased; evidence panel shows what closed each. Click an item to jump to its day. | ≤12 words per day |
| 3 | Try it yourself | Live console embedded and clickable, three hint steps (Verify → next opens → Undo). | ~15 words |
| 4 | Four rules | Four looping mini-animations, one line each. | 4 lines |
| 5 | Numbers | 20–62% · 38% · 2·7·30 days, with sources. | captions |
| 6 | Built with | HL7 · FHIR R4 · SMART · MongoDB Atlas · LangGraph · ElevenLabs | 1 line |
| 7 | Footer | Demo, walkthrough, sources, synthetic-data note. | — |

Budget: under 250 words for the whole page. No diary paragraphs, card grids, accordions, dark bands, stock imagery or invented proof.

## Accessibility
Video muted with a visible pause button and `prefers-reduced-motion` poster; slider is a native range input with keyboard support and a live text label; embedded demo has a "Open full demo" fallback; all animations stop under reduced motion.
