# Demo conversation - Clinic OS v3

Sample business: CarePoint Clinic & Dental Studio (fictional sample data).
Mode: built-in brain (no API key) - the same replies the WhatsApp webhook gives.

**Patient:** hello
**Agent:** Assalam-o-Alaikum! Welcome to CarePoint Clinic & Dental Studio, Faisalabad. Family medicine, dentistry and skin care under one roof. I can share timings and fees, book an appointment, check you in with your Booking ID, or issue a GP/lab token so you skip the queue. How can I help you today?

**Patient:** what are your fees?
**Agent:** Our services and fees:
- General Consultation (GP) - PKR 800 (about 15 min)
- Dental Checkup & Cleaning - PKR 2,500 (about 30 min)
- Dental Filling - PKR 4,000 (about 45 min)
- Root Canal (per visit) - PKR 6,000 (about 60 min)
- Skin Consultation - PKR 1,500 (about 20 min)
- Blood Tests (lab) - Free (about 10 min) | Lab rates vary by test; reports in 24 hours

**Patient:** book
**Agent:** Sure, let's book your appointment. Which service do you need? Reply with the number:
1. General Consultation (GP) - PKR 800 (about 15 min)
2. Dental Checkup & Cleaning - PKR 2,500 (about 30 min)
3. Dental Filling - PKR 4,000 (about 45 min)
4. Root Canal (per visit) - PKR 6,000 (about 60 min)
5. Skin Consultation - PKR 1,500 (about 20 min)
6. Blood Tests (lab) - Free (about 10 min) | Lab rates vary by test; reports in 24 hours

**Patient:** 2
**Agent:** Dental Checkup & Cleaning with Dr. Bilal Ahmed (Mon-Sat 3 PM - 9 PM). Which date? You can say 'today', 'tomorrow', a day name, or a date like '14 Nov'.

**Patient:** today
**Agent:** And what time on Tue 10 Nov 2026? (Dr. Bilal Ahmed sees patients 3:00 PM - 9:00 PM)

**Patient:** 6 pm
**Agent:** And the patient's full name, please?

**Patient:** Ahmad Raza
**Agent:** Please confirm: Dental Checkup & Cleaning on Tue 10 Nov 2026 at 6:00 PM for Ahmad Raza. Reply YES to confirm or NO to cancel.

**Patient:** no, book later
**Agent:** Theek hai, I have not booked anything. Jab ready hon, 'book' likh dein.

**Patient:** book
**Agent:** Sure, let's book your appointment. Which service do you need? Reply with the number:
1. General Consultation (GP) - PKR 800 (about 15 min)
2. Dental Checkup & Cleaning - PKR 2,500 (about 30 min)
3. Dental Filling - PKR 4,000 (about 45 min)
4. Root Canal (per visit) - PKR 6,000 (about 60 min)
5. Skin Consultation - PKR 1,500 (about 20 min)
6. Blood Tests (lab) - Free (about 10 min) | Lab rates vary by test; reports in 24 hours

**Patient:** 2
**Agent:** Dental Checkup & Cleaning with Dr. Bilal Ahmed (Mon-Sat 3 PM - 9 PM). Which date? You can say 'today', 'tomorrow', a day name, or a date like '14 Nov'.

**Patient:** today
**Agent:** And what time on Tue 10 Nov 2026? (Dr. Bilal Ahmed sees patients 3:00 PM - 9:00 PM)

**Patient:** 6 pm
**Agent:** And the patient's full name, please?

**Patient:** Ahmad Raza
**Agent:** Please confirm: Dental Checkup & Cleaning on Tue 10 Nov 2026 at 6:00 PM for Ahmad Raza. Reply YES to confirm or NO to cancel.

**Patient:** yes
**Agent:** Booked! Your Booking ID is BK-44F21121.
Dental Checkup & Cleaning on Tue 10 Nov 2026 at 6:00 PM for Ahmad Raza.
Please arrive a little early. When you reach the clinic, send 'Check in BK-44F21121' here and I'll give you your token — no need to queue at the desk.

**Patient:** check in BK-44F21121
**Agent:** Checked in! Token T-01 for Ahmad Raza — Dental Checkup & Cleaning with Dr. Bilal Ahmed. estimated wait about 0 minutes Please take a seat; your token will be called in order.

**Patient:** token
**Agent:** Instant tokens are for GP and Lab only (dental and skin need an appointment). Which token do you need?
1. General Consultation (GP) or 2. Blood Tests (lab)

**Patient:** 1
**Agent:** Your name for the General Consultation (GP) token, please?

**Patient:** Ahmad Raza
**Agent:** Token T-02 issued for Ahmad Raza — General Consultation (GP) with Dr. Ayesha Khan.
Patients ahead of you: 0 (estimated wait about 0 minutes).
Please come to the clinic and tell reception your token number; they will mark you arrived and your turn will come in order.

**Patient:** i have severe chest pain
**Agent:** This sounds like an emergency. Please call Rescue 1122 right now, or go to the nearest hospital emergency. Do not wait for a chat reply.

**Patient:** talk to a human
**Agent:** I've alerted reception — a staff member will get back to you here shortly. For anything urgent, please call 041-8710000 or WhatsApp 0300-0000000 (reception is staffed during clinic hours).
