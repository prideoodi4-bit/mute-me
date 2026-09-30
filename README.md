# Pride Group Manager Bot

بوت Telegram لإدارة الكروبات، جاهز لـ Railway.

## الوظائف

- العضو يكتب: `برايد امسح رسائلي`
  - البوت يحذف كل رسائل هذا العضو التي قام البوت بتسجيلها وما زالت ضمن مدة الحذف التي يسمح بها Telegram.
  - يستخدم `deleteMessages` على دفعات حتى 100 رسالة في الطلب الواحد.
- العضو يكتب: `برايد اكتمني ساعة`
  - يدعم أيضاً `30 دقيقة`، `ساعتين`، `يوم`، `أسبوع`، وكذلك `30m / 2h / 1d / 1w`.
  - البوت يستخدم `restrictChatMember` كتقييد حقيقي؛ العضو لا يستطيع إرسال الرسائل خلال المدة.
  - الكتم ينتهي تلقائياً من Telegram.
- زر Inline للإدمن: `🔓 إلغاء الكتم — Admin`.
- أوامر Admin بالرد على رسالة العضو:
  - `/mute 30m`
  - `/unmute`
  - `/clearuser`
  - `/status`
  - `/check`
- أوامر العضو:
  - `/clearme`
  - `/help`
- قاعدة SQLite لحفظ Message IDs وبيانات الأعضاء التي شاهدها البوت.
- Health endpoint على `/` و `/health` مناسب لـ Railway.

## حدود Telegram المهمة

1. Bot API يسمح بحذف الرسالة إذا كان عمرها أقل من 48 ساعة.
2. البوت لا يستطيع حذف رسائل قديمة لم يستلمها/يسجل Message ID الخاص بها.
3. يجب أن يكون البوت Admin في الكروب ومعه:
   - Delete messages
   - Restrict members
4. لا يمكن للبوت كتم مالك الكروب أو Admin آخر.

## تشغيل محلي

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

ضع متغير البيئة:

```env
BOT_TOKEN=YOUR_NEW_TOKEN
DATABASE_PATH=./pride_bot.db
PORT=8080
```

ثم:

```bash
python bot.py
```

## الرفع إلى Railway

1. ارفع المشروع إلى Railway (GitHub أو Upload حسب طريقتك).
2. Variables:
   - `BOT_TOKEN` = توكن البوت.
   - `DATABASE_PATH` = `/data/pride_bot.db` إذا أضفت Volume.
3. أضف Railway Volume واربطه على `/data` حتى تبقى قاعدة البيانات بعد Redeploy/Restart.
4. Deploy.
5. أضف البوت للكروب وصعّده Admin.
6. فعّل له `Delete messages` و `Restrict members`.
7. داخل الكروب نفّذ `/check` وتأكد أن كل الصلاحيات ✅.

> ملاحظة: لأن البوت Admin فهو يستلم رسائل الكروب حتى مع Privacy Mode. ويمكنك أيضاً تعطيل Privacy Mode من BotFather إذا أردت، لكن إذا غيرتها بعد إضافة البوت فـ Telegram يوصي بإعادة إضافته للكروب.

## أمان التوكن

لا تضع BOT_TOKEN داخل ملفات المشروع أو GitHub. إذا سبق أن شاركت توكن في مكان عام أو محادثة، الأفضل إلغاؤه من BotFather وإصدار توكن جديد ثم وضعه فقط في Railway Variables.
