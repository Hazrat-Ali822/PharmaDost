# PharmaDost — Roz ka Workflow (A se Z)

Yeh guide batati hai ke **naya patient aaye to kahan se shuru karein**, kaunsa module
kis waqt use hota hai, aur banda **medicine lekar jaane tak** kaise handle hota hai —
aur ek banda jo **sirf pharmacy** ke liye aaye uska bhi poora flow.

> Agar aap akele admin ho (koi aur user nahi banaya) to yeh saare steps **aap khud** karte
> ho — kyunki admin ke paas har module ka access hai. Staff hoga to kaam baant jaayega.

Login ke baad har module baayein taraf **sidebar** se khulta hai.

---

## 🏥 SCENARIO A — Hospital patient (checkup → test → dawai → payment)

### Step 1 — Patient register karo (naya banda)
- Sidebar → **Patients** → **➕ Add Patient**
- Naam, gender, umar, phone, allergies (agar ho) bhar ke **Save**
- Har patient ko ek **MRN** (permanent ID) mil jaata hai
- **Purana patient?** Register mat karo — bas **Patients** list mein naam/phone se dhoondo
  aur uska naam kholo.

> Patient ka naam kholte hi **Patient History** page aata hai — yehi is patient ka
> "control center" hai. Yahan se aage sab kuch ho jaata hai (bill, lab, imaging).

### Step 2 — Doctor ke saath appointment lagao (consultation)
- Sidebar → **OPD → Appointments** → **➕ New Appointment**
- Patient chuno, **Doctor** chuno, visit type (New / Follow-up), **Save**
- ✅ **Yahin automatic** doctor ki **consultation fee** ka ek bill (pending) ban jaata hai —
  aapko alag se fee likhne ki zaroorat nahi.

### Step 3 — Doctor checkup + prescription
- Doctor patient dekhta hai, phir usi appointment se **Prescription** likhta hai
  (OPD → Appointments → us appointment ko kholo → dawaiyan add karo)
- Diagnosis + medicines list save ho jaate hain — sab patient ki history mein aa jaata hai

### Step 4 — Agar test / scan chahiye (optional)
Patient ka **History page** kholo, upar buttons se:
- **➕ Order Lab Test** → test chuno (CBC, sugar, waghera)
  - Lab wala result bhar ke **Save & Print Report** dabata hai → report print + history mein save
  - ✅ Lab fee ka bill (pending) automatic ban jaata hai
- **➕ Imaging Study** → Ultrasound / X-Ray / CT / MRI register karo
  - Sonographer findings/impression bharta hai → **Print report**
  - ✅ Imaging fee ka bill (pending) automatic ban jaata hai

### Step 5 — Pharmacy se dawai do
- Sidebar → **Pharmacy / Sales** → **🛒 New Sale**
- Medicines add karo (stock FEFO se khud kam hota hai)
- Chaho to **isi patient ko link** kar do — taake dawai bhi uski history + grand bill mein aaye
- Retail/Wholesale rate select karo (wholesale sirf registered customer ke liye)

### Step 6 — Ek hi Grand Total bill + payment 💵
Yeh sabse aham step — sab kuch **ek jagah**:
- Patient **History page** → **💵 Bill / Collect Payment**
- Yahan ek hi screen par:
  - **Consultation + Lab + Imaging + (linked) medicines** sab jama
  - **Total charged / Paid / Outstanding** teen tiles
- Neeche **Collect Payment** box: jitna paisa banda de raha hai woh amount + method (Cash/Card)
  daal ke **Collect** — system khud **purane bakaya se pehle** adjust karta hai (oldest-first)
- **Print** button se patient ko ek saaf **grand receipt** de do
- ✅ Har payment ka record rehta hai (kisne kitna diya, kab) — outstanding bhi dikhta hai

### Step 7 — Banda chala gaya ✅
- History page par uska **poora record** hamesha rehta hai: visits, prescriptions, lab
  results, imaging, medicines, bills, payments.
- Agla dafa aaye to **register nahi** karna — bas naam kholo, sab saamne.

---

## 💊 SCENARIO B — Sirf Pharmacy customer (walk-in)

Yeh sabse chhota flow — na patient register, na appointment.

### Retail (aam customer)
1. Sidebar → **Pharmacy / Sales** → **🛒 New Sale**
2. Sale type = **Retail**, Customer = khaali chhod do (walk-in)
3. Medicines add karo → quantity → **Retail price** khud lag jaata hai
4. Total dikhega → paisa lo → **Save / Complete Sale**
5. **Bill print** kar ke customer ko de do. Stock khud kam ho gaya. Bas.

### Wholesale (registered customer ko)
1. Pehli baar ho to: Sidebar → **Customers** → **➕ Add Customer** (naam/phone)
2. **New Sale** → Sale type = **Wholesale** → **Customer chuno** (zaroori!)
3. Ab har item ka **wholesale rate** lagta hai
   - ⚠️ Wholesale price **sirf tab** milta hai jab customer registered ho **aur** wholesale
     select ho — warna system rokega.
4. Complete Sale → print.
5. Udhaar (credit) diya ho to woh customer ke khaate mein outstanding rehta hai; baad mein
   payment collect kar sakte ho.

> Sirf-pharmacy business (koi hospital nahi)? To Setup/Settings mein **sirf Pharmacy module**
> on rakho — patients/OPD/lab sab gayab, screen bilkul saaf pharmacy-only.

---

## 🔁 Jaldi yaad rakhne ke liye (cheat sheet)

| Kaam | Kahan jao |
|---|---|
| Naya patient | Patients → Add Patient |
| Purana patient dhoondo | Patients → naam/phone search |
| Doctor fee / checkup | OPD → Appointments → New (fee auto-bill) |
| Prescription | Appointment kholo → dawaiyan add |
| Lab test | Patient History → Order Lab Test |
| Ultrasound/X-ray | Patient History → Imaging Study |
| Dawai bechna | Pharmacy → New Sale |
| **Sab ka ek bill + payment** | Patient History → **💵 Bill / Collect Payment** |
| Sirf pharmacy grahak | Pharmacy → New Sale (patient ki zaroorat nahi) |
| Din ke end ka hisaab | Reports → Day Book / Cash Closing |

---

## Soch samajhne ka asaan usool 🧠
- **Patient History page = hospital ka dil.** Ek dafa patient khol lo, aage lab/imaging/bill
  sab wahin se — idhar-udhar nahi bhatakna.
- **Har fee (consultation/lab/imaging) khud pending bill ban jaati hai** — aapko manually
  bill nahi banana. Aap sirf **end par collect** karte ho.
- **Pharmacy walk-in ka patient se koi taalluq nahi** — seedha New Sale, print, done.
- Sab modules **ek admin** bhi chala sakta hai; staff banao ge to bas kaam baant jaayega.
