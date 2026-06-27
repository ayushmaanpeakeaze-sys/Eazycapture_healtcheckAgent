# Duplicate Detection — Kaise Kaam Karta Hai (Explainer)

> Ye doc **logic + design** samjhata hai — sir ka low-level design, aur humne usko
> kaise product-grade banaya. (API/frontend ke liye alag doc hai:
> `FRONTEND_DUPLICATE_INVOICES.md`.) Last updated: 2026-06-19.

---

## 1. Duplicate kya hai? (sir ka definition)

Sabse khatarnaak duplicate — ek hi invoice **do baar** enter ho gayi, aur:

- ✅ **Same invoice number**
- ✅ **Same amount**
- ✅ **Same customer**
- ✅ **Ek paid / bank matched** (paisa chala gaya)
- ✅ **Doosri abhi outstanding** (pending padi)

Ye sir ka core hai. Iske around humne thoda aur capability add ki (settings,
tiers, risk) — par foundation yahi hai.

---

## 2. Design — 3 layers (receivable/payable → contact → match)

Sir ka low-level design ek **funnel** jaisा hai:

```
SAARE documents
 │
 ├─ RECEIVABLES (ACCREC — sales invoices, customer tum pe owe karta)
 │    ├─ Contact A (customer) → in invoices mein match dhoondho
 │    ├─ Contact B (customer) → match dhoondho
 │    └─ …
 │
 └─ PAYABLES (ACCPAY — supplier bills, tum supplier ko owe karte)
      ├─ Contact A (supplier) → match dhoondho
      └─ …
```

**Pehle direction (receivable/payable) alag → uske andar contact-wise → phir match.**
Ek receivable kabhi payable ka duplicate nahi ho sakta; duplicate hamesha **same
contact** ke andar hota hai.

Code mein:
| Layer | Code |
|---|---|
| Receivable vs Payable alag | `if type_a != type_b: continue` (ACCREC sirf ACCREC se) |
| Contact-wise group | `by_contact[_contact_key(tx)]` |
| Match within | scoring → 100% / 95% / … |

---

## 3. ContactID = Foreign Key (isi se group karte)

Har invoice/bill ek **contact** se juda hota — Xero use `Contact.ContactID` se store
karta. Database terms mein:

```
contacts table              invoices table
──────────────              ──────────────
contact_id (PK)  ◄────────── contact_id (FK)
name                         invoice_id (PK)
email                        amount
                             date …
```

- contacts mein `contact_id` = **Primary Key** (har contact unique)
- invoices mein `contact_id` = **Foreign Key** (ye invoice kiski hai)

**Receivable ka contact = customer, payable ka contact = supplier.** Hum guess nahi
karte — Xero har invoice mein ContactID deta hai, hum bas padh lete hain.

```python
# har invoice ka grouping key — yahi foreign key
key = _contact_key(tx)      # = tx.contact_id (merged ho to canonical)
```

**Example:**
```
INV-3300  £2400  Contact = Northgate (abc-123)   ← receivable
INV-3301  £2400  Contact = Northgate (abc-123)   ← same customer → same group
BILL-88   £430   Contact = Office Supplies (xyz)  ← payable, alag group
```
INV-3300 + INV-3301 → same ContactID → ek group → inme duplicate check.

---

## 4. Duplicate Contacts SABSE PEHLE (cascade)

Ek catch: agar **ek hi vendor do alag contact records** mein hai (C1, C2 — dono
"Ayushmaan"), to uski invoices alag groups mein bant jaati → duplicate **chhoot
jaata**:

```
INV-100 → contact_id = C1 (Ayushmaan)
INV-101 → contact_id = C2 (Ayushmaan duplicate)
            ↑ alag FK → alag group → duplicate MISS!
```

Isiliye **pehle Duplicate Contacts** chalta → C1 aur C2 ko **merge** (alias) →
ab dono invoices ek hi (canonical) contact_id → ek group → duplicate **pakda jaata**.

Code: `_duplicate_contacts` → `duplicate_contact_pairs` → `_build_contact_alias`
→ wahi alias `_find_duplicate_bills` ko milta. **FK clean hona zaroori hai pehle.**

---

## 5. Multi-tenant (ek company ka data doosri se mix na ho)

Tool multi-tenant hai. Do keys matter karte:

```
(org_id, contact_id)
   ↑         ↑
 tenant    contact
```

Hamare yahan **org isolation fetch ke time** ho jaata — har audit sirf ek company
ke transactions laata (`WHERE company_id = …`). To group key mein sirf `contact_id`
kaafi (org already alag). Net: kabhi cross-company mix nahi.

---

## 6. Optimization — har invoice ko har doosri se compare NAHI karte

| Operation | Karte hain? |
|---|---|
| Har invoice ek baar **padhna** (bucket banane ko) | Haan — O(n), zaroori |
| Har invoice ko har doosri se **compare** | **Nahi** — sirf same bucket |

Compare sirf unka jo: **same contact + same type + same/near date** (default window
0 = same din). Baaki sab skip.

**1000 invoices ka farak:**
| Tareeka | Comparisons |
|---|---|
| Naive (har pair) | ~5,00,000 😵 |
| Hamara (contact + same-day bucket) | chand hazaar ⚡ |

Zyaadatar invoice apne (contact, din) slot mein **akeli** → koi comparison nahi.
Yahi sir wala "sabko sabse mat compare karo, group karke dekho."

---

## 7. Confidence — kitna pakka duplicate hai (tiers)

Default window = **0 din = same issue date**. Pattern ke hisaab se fixed confidence:

| Pattern | Confidence | Tier |
|---|---|---|
| Same invoice number + amount, same day (sab match) | **100%** | 🔴 high |
| Different number, **same day**, baaki same | **95%** | 🔴 high + "2 distinct docs?" |
| Same reference + amount, number nahi | 90% | 🔴 high |
| Different number + **day gap** (window badha) | 70% | 🟡 review |
| Different **amount** | 65% | 🟡 review |
| Weak — sirf amount + customer + same day | 75% | 🟡 review |
| Recurring / subscription cadence | 45% | ⚪ review |

- **Reference dono mein nahi → phir bhi duplicate** (number/amount se pakad lete)
- **Dono unpaid → phir bhi duplicate** (paid koi shart nahi)
- "Sab dikhana hai" — jo poora match na ho usko bhi **review** mein dikha dete

---

## 8. 🏦 Bank-matched (risk signal)

Do alag cheezein, dono har row pe:

| Field | Matlab | Source |
|---|---|---|
| **Paid** | payment record ho gaya (status PAID / amount_due 0) | invoice ke saath aata |
| **Bank reconciled** | payment **bank statement se match** — paisa sach mein gaya | Xero `Payments.IsReconciled` |

**Sabse khatarnaak duplicate:** ek copy **paid + bank reconciled**, doosri **abhi
outstanding** → paisa ek pe already chala gaya. Engine isko `risk: "high"` mark
karta (⚠️ "one already paid") aur upar dikhata.

> Risk se ye decide **nahi** hota ki duplicate hai ya nahi (dono unpaid bhi
> duplicate hote) — sirf **urgency** dikhती hai.

---

## 9. Settings — sab configurable (per client)

| Setting | Default | Matlab |
|---|---|---|
| `duplicate_days_window` | **0** | kitne din apart chale — 0 = same din; 1/2/N badha sakte |
| `duplicate_require_same_amount` | true | amount same ho (off → alag amount bhi surface ho) |
| `duplicate_require_exact_reference` | true | conflicting reference drop (no-ref phir bhi match) |
| `duplicate_also_check_paid` | false | paid invoices include (off → kam se kam ek unpaid) |

Default sir ke "same din, sab match" pe set hai; client chahe to loosen kar sakta.

---

## 10. Sir ka design vs hamara — ek hi cheez ke 2 layers

**Sir ka level = LOW-LEVEL DESIGN (engine ka core):**
direction split → ContactID (FK) group → sab match → duplicate. ✅ Bilkul correct
foundation.

**Hamara level = PRODUCT (sir ke core ke upar):**
- Sir ka "sab match" = humara **100% HIGH** (untouched)
- **+ Settings** (configurable), **+ tiers/review** (kuch chhute na),
  **+ bank-matched risk**, **+ 100% accurate confidence**

> **Sir ka design = humara FOUNDATION. Hum usi pe khade hain — bas product-grade
> bana diya. Contradiction zero.**

### Sir ko ek line mein
> "Sir, aapka core — receivable/payable alag, ContactID foreign key se group,
> duplicate contacts pehle merge, phir sab match toh duplicate — humne exactly
> waise hi banaya; wo humara 100% confident case hai. Uske upar settings + risk +
> review layer add kiya taaki real clients ke liye flexible aur safe rahe."
