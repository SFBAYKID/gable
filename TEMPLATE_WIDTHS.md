# Template text-box widths — what needs changing in Canva

Measured from the templates themselves on 2026-08-11, not estimated. Every
number below is in inches at **that design's own font size**, so the change is
mechanical: widen the box, leave the type alone.

## Why

The boxes are sized to fit the *placeholder words* — `Phone`, `Email`,
`Website` — not the values that replace them. A real website URL needs about
six times the width of the word "Website". Google Slides does not clip; it
soft-wraps, so a phone number breaks mid-digit and an address collides with
the panel below it.

Gable will not paper over this at render time. Shrinking the type to fit is
what produced the 8pt contact rows, and a runtime nudge would be undone by
the next export. Fixed once in the source, it stays fixed.

## The rule, as standard practice

**Size every dynamic text box for its longest realistic value, not for the
placeholder.** The values used to compute these targets are the longest
currently on the roster:

| field | longest real value |
|---|---|
| Phone | `443.854.8554` |
| Email | `ericapfeiffer@cornerhouserealty.com` |
| Website | `https://cornerhouserealty.com/loletha-simmons/` |
| Address | `7940 Oakwood Rd, Glen Burnie, MD 21061` |
| Price | `$1,100,000` |
| Agent name | `Bobby Carr` |

Targets include a 6% safety margin. Keep the left edge where it is and grow
the box to the right — the left edges are shared down a contact column and
moving one breaks the alignment.

## The list — 97 boxes across 24 designs

### Coming Soon — Be the First to Know

| field | now | needs | grow by |
|---|---:|---:|---:|
| [PROPERTY ADDRESS] | 4.44in | **9.14in** | +4.71in |

### Coming Soon — Beds, Baths, SqFt, Garage

| field | now | needs | grow by |
|---|---:|---:|---:|
| [PROPERTY ADDRESS] | 4.65in | **8.06in** | +3.41in |
| Email | 0.56in | **3.90in** | +3.34in |
| Phone | 0.65in | **1.56in** | +0.91in |
| Realtor | 0.78in | **0.84in** | +0.05in |

### Coming Soon — Exclusive Preview, Get on the List

| field | now | needs | grow by |
|---|---:|---:|---:|
| Website | 0.96in | **5.88in** | +4.91in |
| Email | 0.66in | **4.59in** | +3.93in |
| [PROPERTY ADDRESS] | 4.35in | **7.53in** | +3.18in |
| Phone | 0.76in | **1.83in** | +1.08in |
| Realtor | 0.78in | **0.84in** | +0.05in |

### Coming Soon — Fall in Love First

| field | now | needs | grow by |
|---|---:|---:|---:|
| Website | 0.96in | **5.88in** | +4.91in |
| Email | 0.66in | **4.59in** | +3.93in |
| [PROPERTY ADDRESS] | 4.73in | **8.20in** | +3.48in |
| Phone | 0.76in | **1.83in** | +1.08in |
| Realtor | 1.02in | **1.09in** | +0.07in |

### Coming Soon — Something Great Is On the Way

| field | now | needs | grow by |
|---|---:|---:|---:|
| Website | 0.96in | **5.88in** | +4.91in |
| [PROPERTY ADDRESS] | 4.44in | **9.14in** | +4.71in |
| Email | 0.66in | **4.59in** | +3.93in |
| Phone | 0.76in | **1.83in** | +1.08in |
| Realtor | 1.02in | **1.09in** | +0.07in |

### Coming Soon — VIP Exclusive Preview

| field | now | needs | grow by |
|---|---:|---:|---:|
| [PROPERTY ADDRESS] | 4.44in | **9.14in** | +4.71in |

### Corner House Realty — Just Listed — Bracket Placeholders

| field | now | needs | grow by |
|---|---:|---:|---:|
| [PROPERTY ADDRESS] | 6.91in | **12.60in** | +5.69in |
| Website | 0.81in | **4.89in** | +4.09in |
| Email | 0.55in | **3.82in** | +3.27in |
| [PRICE] | 1.47in | **2.41in** | +0.94in |
| Phone | 0.63in | **1.52in** | +0.89in |
| Realtor | 0.96in | **1.03in** | +0.07in |

### Just Listed — Bracket Placeholders (cleanest)

| field | now | needs | grow by |
|---|---:|---:|---:|
| [PROPERTY ADDRESS] | 6.91in | **12.60in** | +5.69in |
| Website | 0.81in | **4.89in** | +4.09in |
| Email | 0.55in | **3.82in** | +3.27in |
| [PRICE] | 1.47in | **2.41in** | +0.94in |
| Phone | 0.63in | **1.52in** | +0.89in |
| Realtor | 0.96in | **1.03in** | +0.07in |

### Just Rented — Per Month (mis-filed under Just Sold)

| field | now | needs | grow by |
|---|---:|---:|---:|
| Realtor | 1.12in | **1.19in** | +0.07in |

### Just Sold — Let's Connect

| field | now | needs | grow by |
|---|---:|---:|---:|
| Website | 0.81in | **4.89in** | +4.09in |
| Email | 0.55in | **3.82in** | +3.27in |
| [PROPERTY ADDRESS] | 4.44in | **7.09in** | +2.65in |
| [PRICE] | 2.23in | **3.66in** | +1.43in |
| Phone | 0.63in | **1.52in** | +0.89in |
| Realtor | 0.96in | **1.03in** | +0.07in |

### Just Sold — Local Experts, Address Twice

| field | now | needs | grow by |
|---|---:|---:|---:|
| Website | 0.81in | **4.89in** | +4.09in |
| Email | 0.55in | **3.82in** | +3.27in |
| [PROPERTY ADDRESS] | 3.72in | **6.79in** | +3.07in |
| [PROPERTY ADDRESS] | 3.72in | **6.79in** | +3.07in |
| [PRICE] | 2.60in | **4.27in** | +1.66in |
| Phone | 0.63in | **1.52in** | +0.89in |
| Realtor | 0.96in | **1.03in** | +0.07in |

### Just Sold — Sold For, Agent Card

| field | now | needs | grow by |
|---|---:|---:|---:|
| Website | 0.97in | **5.88in** | +4.91in |
| [PROPERTY ADDRESS] | 4.51in | **8.79in** | +4.28in |
| Email | 0.66in | **4.59in** | +3.93in |
| [PRICE] | 1.84in | **3.28in** | +1.44in |
| Phone | 0.76in | **1.83in** | +1.07in |
| Realtor | 1.16in | **1.24in** | +0.08in |

### Just Sold — With Beds, Baths and SqFt

| field | now | needs | grow by |
|---|---:|---:|---:|
| Website | 0.81in | **4.89in** | +4.09in |
| Email | 0.55in | **3.82in** | +3.27in |
| [PROPERTY ADDRESS] | 3.72in | **6.79in** | +3.07in |
| [PRICE] | 2.60in | **4.27in** | +1.66in |
| Phone | 0.63in | **1.52in** | +0.89in |
| Realtor | 0.96in | **1.03in** | +0.07in |

### Meet the Agent — Find Your Next Home, Specialty

| field | now | needs | grow by |
|---|---:|---:|---:|
| Website | 1.31in | **7.97in** | +6.66in |
| Email | 0.89in | **6.23in** | +5.34in |
| Phone | 1.04in | **2.48in** | +1.44in |

### Meet the Agent — Fun Fact and Why Clients Love Me

| field | now | needs | grow by |
|---|---:|---:|---:|
| Website | 1.31in | **7.97in** | +6.66in |
| Email | 0.89in | **6.23in** | +5.34in |
| Phone | 1.04in | **2.48in** | +1.44in |

### Meet the Agent — Let's Connect, Local Roots

| field | now | needs | grow by |
|---|---:|---:|---:|
| Website | 1.31in | **7.97in** | +6.66in |
| Email | 0.89in | **6.23in** | +5.34in |
| Phone | 1.04in | **2.48in** | +1.44in |

### Neighborhood — Explore — Three Local Favorites

| field | now | needs | grow by |
|---|---:|---:|---:|
| Website | 0.81in | **4.89in** | +4.09in |
| Email | 0.55in | **3.82in** | +3.27in |
| Phone | 0.63in | **1.52in** | +0.89in |

### Open House — Book a Private Tour

| field | now | needs | grow by |
|---|---:|---:|---:|
| Email | 0.45in | **3.11in** | +2.66in |
| [PROPERTY ADDRESS] | 2.72in | **5.28in** | +2.56in |
| Phone | 0.51in | **1.24in** | +0.73in |
| Realtor | 0.78in | **0.84in** | +0.05in |

### Open House — Join Us This Weekend

| field | now | needs | grow by |
|---|---:|---:|---:|
| Website | 0.81in | **4.89in** | +4.09in |
| Email | 0.55in | **3.82in** | +3.27in |
| [PROPERTY ADDRESS] | 2.53in | **4.93in** | +2.40in |
| Phone | 0.63in | **1.52in** | +0.89in |
| [PRICE] | 0.85in | **1.39in** | +0.54in |
| Realtor | 0.78in | **0.84in** | +0.05in |

### Open House — Weekend — Come Tour With Us (Sat + Sun)

| field | now | needs | grow by |
|---|---:|---:|---:|
| Website | 0.81in | **4.89in** | +4.09in |
| Email | 0.55in | **3.82in** | +3.27in |
| [PROPERTY ADDRESS] | 3.10in | **6.21in** | +3.11in |
| Phone | 0.63in | **1.52in** | +0.89in |
| [PRICE] | 0.80in | **1.39in** | +0.59in |
| Realtor | 0.78in | **0.84in** | +0.05in |

### Under Contract — After Multiple Offers

| field | now | needs | grow by |
|---|---:|---:|---:|
| Email | 3.27in | **4.59in** | +1.32in |
| Phone | 1.51in | **1.83in** | +0.32in |
| Realtor | 1.02in | **1.09in** | +0.07in |

### Under Contract — Agent Card

| field | now | needs | grow by |
|---|---:|---:|---:|
| Email | 3.78in | **5.31in** | +1.53in |
| Phone | 1.74in | **2.12in** | +0.37in |
| Realtor | 1.18in | **1.26in** | +0.08in |

### Under Contract — Sold For

| field | now | needs | grow by |
|---|---:|---:|---:|
| [PROPERTY ADDRESS] | 2.84in | **5.18in** | +2.33in |
| [PRICE] | 2.23in | **3.66in** | +1.43in |
| Realtor | 0.87in | **0.93in** | +0.06in |

### Under Contract — Thinking of Selling

| field | now | needs | grow by |
|---|---:|---:|---:|
| [PROPERTY ADDRESS] | 4.54in | **8.43in** | +3.89in |

## After the change

Re-export and drop the files in the Drive folders as usual. Gable detects the
change, re-measures every template automatically, and reports anything that
still does not fit. Re-onboarding is routine — it is not a reason to hold off
on an export.
