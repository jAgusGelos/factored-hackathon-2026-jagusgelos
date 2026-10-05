# Launch video: narration and shot list

Target length 2:20-2:30 (1920x1080, 30 fps). The narration is spoken by the builder on camera, in
the first person; each line is one take (T01-T17, see `docs/pitch/RECORDING.md`), placed as a full
presenter cut-out, a picture-in-picture bubble, voice only, or beside the diagram. Until the takes
are in, MoneyPrinterTurbo's TTS voice is a timing placeholder. Story: Why (the wait) → What (three moments of the
product) → How (one trust beat) → the promise. 90% product, 10% technical.

`video/narration/lines.json` is the source of truth for the voiceover and this document mirrors
it; `video/scripts/make_vo.py` turns those lines into audio with MoneyPrinterTurbo's TTS
(edge-tts voice `en-US-AndrewNeural`, rate 0.95) and writes word timings to
`video/src/meta/vo.json`. Times below are where each line starts in the edit
(`video/src/timeline.ts` is authoritative); the duration in brackets is the measured length of the
generated audio.

Every on-screen number carries its honesty label and comes from the repo:
37 h median first response (MEASURED, `docs/analysis/demand-report.md`, n = 7,567 of 12,297),
130,690 transaction rows extracted from 2026-05-18 to 2026-06-17 out of ~5M (MEASURED,
`data/extraction_manifest.json`; the 5M / 808 MB size is in `docs/architecture-decisions.md`,
dispute-agent AD-2), 40 scenarios with 0 unsafe outcomes (SIMULATED,
`README.md` "Evaluation results"), resolving turn 1.3-6.6 s with Claude Haiku 4.5 (MEASURED, manual
runs, `README.md`).

The app's UI is Spanish/Portuguese (challenge requirement); every chat bubble on screen gets an
English caption.

---

## 1. Cold open (0:00-0:21): the wait

No UI. Black, then a phone in the dark.

| Time | Narration | Picture |
|---|---|---|
| 0:01 | **T01** (presenter) "It's late. You open your bank app, and there it is. A charge you don't recognize." [5.2 s] | Stock b-roll (Pexels via MoneyPrinterTurbo), dark and slow: a person at night with a phone. Over it, a phone mockup lights up with a push notification: "Nueva compra · Tienda Online Global" (caption: "New purchase"). Kinetic word: **"Unrecognized."** |
| 0:07 | **T02** (presenter) "You report it. And then, you wait." [2.7 s] | The phone dims. A single line of type: "Report sent." Then silence. |
| 0:11 | (beat) | The **37 h clock**: a thin ring sweeps while an hour counter races 0 → 37. |
| 0:12 | **T03** (presenter) "At this bank, the median wait for a first answer is thirty-seven hours." [4.6 s] | The counter lands on **37 h**. Label: "MEASURED · median first response to 'Cargo no reconocido' complaints · n = 7,567 of 12,297". |

## 2. Reveal (0:21-0:35): the product

| Time | Narration | Picture |
|---|---|---|
| 0:22 | **T04** (presenter) "I wanted that answer in seconds. And I wanted it to be right." [3.6 s] | Pure black. The words "seconds" and "right" fade in one at a time, large, centered. |
| 0:28 | **T05** (presenter) "So I built the LATAM Bank Dispute Agent." [2.8 s] | Launch-event reveal: a light sweep, then the name in large type, the tagline underneath: "Resolves what it can prove. Hands off what it can't." |

## 3. What: three moments (0:35-1:36)

Each moment sits in a laptop mockup on a dark stage, real footage of the local app (Playwright,
real Claude Haiku 4.5 calls; model wait time trimmed in the edit, never faked). English captions
float over the Spanish chat. A small chapter label on the left: 01 / 02 / 03.

### 01 Resolves in seconds, with proof (0:35-0:57)

| Time | Narration | Picture |
|---|---|---|
| 0:36 | **T06** (bubble) "The customer writes it in their own words. The agent finds the exact charge in their history, and asks: is this the one?" [7.9 s] | Footage `m1`: the customer types "No reconozco un cargo de 38.500 pesos del 14 de junio" (caption: "I don't recognize a 38,500 peso charge from June 14"). The agent names Uber, amount and date with "Sí, es ese" / "No es ese" (caption: "Is this the one?"). Zoom on the buttons. |
| 0:45 | **T07** (bubble) "One short explanation, and it's done. A provisional credit, the card blocked, a reference number. Every fact is checked against the record." [8.6 s] | "Sí, es ese", the explanation "no uso Uber hace meses, tengo la tarjeta conmigo" (caption), then the resolution and the "Verificación del sistema" card. Zoom on the verified-fact chips. Callout: "Resolving turn 1.3-6.6 s · MEASURED". |

### 02 Asks when it is ambiguous (0:57-1:12)

| Time | Narration | Picture |
|---|---|---|
| 0:58 | **T08** (bubble) "Charged twice for one taxi? It doesn't guess. It shows the customer their own charges, and lets them pick." [6.7 s] | Footage `m2`: "Me cobraron dos veces un taxi de 27 mil" (caption: "I was charged twice for a 27k taxi"). Two charge cards appear; zoom on them; the customer taps one. |
| 1:06 | **T09** (bubble) "Then it finds the twin in the data, and refunds one. Only one." [4.3 s] | "tomé un solo taxi y me lo cobraron dos veces" (caption), then the resolution: one of the two reversed. Kinetic: "1 of 2 reversed." |

### 03 Knows when not to act (1:12-1:36)

| Time | Narration | Picture |
|---|---|---|
| 1:13 | **T10** (bubble) "And when a charge should not be paid automatically, it knows not to act." [4.4 s] | Footage `m3`: "No reconozco una compra en Tienda Online Global" (caption). The agent asks what happened (caption: "Before handing your case over, tell me what happened"). |
| 1:18 | **T11** (voice) "It asks the questions an advisor would call back for. Then a person gets the full case file: verified facts, the customer's story, and what is still open." [9.9 s] | Short answers, the "Caso derivado" notice with case number and the 3-business-day contact deadline (caption), then a cut to the advisor's view ("Vista interna"): the case file slides out of the laptop as layered cards: verified facts, what the customer reported, open questions. |
| 1:30 | **T12** (voice) "In Spanish, or in Portuguese." [2.5 s] | Footage `pt`: the language toggle flips to PT, "Não reconheço uma compra na Tienda Online Global" (caption: same request in Portuguese), the same path. |

## 4. How: the trust beat (1:36-2:09)

Animated diagram on the dark stage, no footage. Three lanes build left to right.

| Time | Narration | Picture |
|---|---|---|
| 1:37 | **T13** (diagram) "Why would a bank trust it? Because the model only reads. The rules and permissions live in code. And every fact is checked against the customer's own ledger." [9.6 s] | "Customer message" → **Model reads** (extracts, summarizes, never decides) → **Code decides** (state machine, policy table, permissions) → **Ledger verifies** (the customer's own charges). The model lane's arrow into "decide" is drawn and then struck through. Headline: "The model reads. The code decides." |
| 1:48 | **T14** (diagram, may-change) "I built it on a focused slice of the data: one month of transactions. A dispute only needs recent history, and the live app never touches the raw data." [10.0 s] | A 5M-row block shrinks to a one-month band: "130,690 of ~5M transaction rows · 2026-05-18 to 2026-06-17 · MEASURED". Three reason chips: "A dispute needs the customer's recent ledger · DESIGN ARGUMENT", "The live app reads one small fixture, no AWS credentials (AD-2)", "Only allowlisted charge facts reach the model (AD-5)". |
| 2:00 | **T15** (diagram, placeholder) "In forty simulated test cases, prompt injection included: zero unsafe outcomes." [6.5 s] | Big **0 / 40** unsafe outcomes, label "SIMULATED · offline harness, mocked model · constructed suite, not held-out". |

## 5. Close (2:09-2:27): the promise

| Time | Narration | Picture |
|---|---|---|
| 2:10 | **T16** (presenter) "Answers in seconds for the cases it should resolve. A person for the ones it shouldn't." [5.3 s] | Split: "37 h" crossed out → "seconds" on the left; a person icon with the case file on the right. |
| 2:17 | **T17** (presenter) "The LATAM Bank Dispute Agent. Try it live. The link is below." [4.8 s] | End card: product name, tagline, live demo URL `factored-hackaton-latest.onrender.com`, repo `github.com/jAgusGelos/factored-hackathon-2026-jagusgelos`, "Factored AI & Data Hackathon 2026". Hold 5 s. |

---

## Footage list (Playwright, local app, 1280x800 viewport, recorded at 2x device scale)

| Clip | Flow (README "The required scenarios") | Used in |
|---|---|---|
| `m1` | Log in (Autocompletar) → type the 38.500 pesos report → "Sí, es ese" → explanation → resolved | 01 |
| `m2` | New chat → "Me cobraron dos veces un taxi de 27 mil" → tap a taxi → explanation → resolved | 02 |
| `m3` | New chat → "No reconozco una compra en Tienda Online Global" → statement answers → escalated → "Vista interna" | 03 |
| `pt` | Toggle PT → "Não reconheço uma compra na Tienda Online Global" | 03 |

What is real and what is illustrative: the app footage is real (local app, real model). The cold
open's phone notification, the 37 h clock and the trust diagram are motion graphics; the stock
b-roll is illustrative and labeled as such in `docs/pitch/VIDEO.md`.
