# مسار عمل استخلاص النصوص ورقمنة الكتب (OCR Workflow)

أدوات مؤتمتة لاستخلاص النصوص العربية والإنجليزية من ملفات PDF الممسوحة ضوئياً، مع الحفاظ على تنسيق الصفحات وحواشيها، وتحويل المخرجات في النهاية إلى مستندات Word منسقة.

---

## المميزات

- **قراءة محلية كاملة**: يقرأ السكربت ملفات PDF محلياً بالاعتماد على أداة `view_file` المدمجة بالنموذج دون استدعاء أي مكتبات OCR خارجية.
- **مسار تشغيل ثنائي**: يعالج السكربت الملفات الصغيرة (20 صفحة فأقل) مباشرة في خطوة واحدة (Fast Track)، ويقسم الملفات الكبيرة إلى أجزاء (Chunks) لا تتجاوز 20 صفحة لمعالجتها بالتوازي.
- **تحديد الصفحات**: يدعم معامل `--pages` معالجة صفحات معينة (مثل `5-15` أو `5,8,10-12`) مع تفكيك النطاقات غير المتصلة تلقائياً إلى أجزاء متصلة للحفاظ على الترقيم الأصلي.
- **تحويل دقيق عبر Pandoc**: يعتمد التحويل إلى Word بالكامل على أداة `pandoc` لضبط العناوين والجداول.
- **اتجاه النص العربي**: يكتشف السكربت وجود الحروف العربية تلقائياً ويحقن المعامل `-M dir=rtl` لضبط المحاذاة من اليمين إلى اليسار.
- **فحص التكرار المشروط**: يفحص السكربت تكرار الأسطر في المستندات الكاملة، ويتجاوز هذا الفحص تلقائياً عند تحديد صفحات مخصصة لمنع تعطل الدمج.
- **تثبيت ذاتي**: يتحقق سكربت التحويل من وجود `pandoc` في النظام ويقوم بتثبيته صامتاً عبر `winget` عند غيابه.
- **تنظيم تلقائي للمخرجات**: يعزل ملفات Markdown في مجلد `md/` وملفات Word في مجلد `word/` لإبقاء جذر المشروع نظيفاً.
- **ضبط النصوص الشرعية والشعرية**: يلتزم بأقواس الرسم العثماني للآيات `﴿ ﴾` والمحاذاة النجمية لأبيات الشعر `*` مع إثبات واصمات التلف المادي والصفحات الفارغة.

---

## معلومات التواصل

- **المؤلف**: كمال ياسر (Kamal Yaser)
- **البريد الإلكتروني**: [kamalyaser31@gmail.com](mailto:kamalyaser31@gmail.com)
- **تليجرام**: [@kamalyaser31](https://t.me/kamalyaser31)

---

## هيكل المجلدات

- **[pdf/](pdf/)**: ملفات PDF المصدر المراد تفريغها.
- **[output_parts/](output_parts/)**: الأجزاء المقسمة المؤقتة وسجل المتابعة `progress.json`.
- **[md/](md/)**: ملفات Markdown النهائية.
- **[word/](word/)**: مستندات Word (.docx) النهائية.
- **[.agents/skills/ocr-transcription/](.agents/skills/ocr-transcription/)**: ملف المهارة `SKILL.md` ومجلد السكربتات المساعدة والمراجع.

---

## خطوات التشغيل

### ١. التمهيد والتقسيم
ضع ملف الـ PDF في مجلد `pdf/` ثم شغل سكربت التقسيم:
```bash
# لمعالجة الملف كاملاً
python .agents/skills/ocr-transcription/scripts/splitt_pdf.py "pdf/input.pdf"

# لمعالجة صفحات محددة فقط
python .agents/skills/ocr-transcription/scripts/splitt_pdf.py "pdf/input.pdf" --pages "5,8,10-12"
```

### ٢. التحقق والدمج
بعد انتهاء استخلاص النصوص للأجزاء في مجلد `output_parts/`، شغل سكربت التحقق ثم الدمج:
```bash
# تشغيل التحقق الهيكلي للأجزاء
python .agents/skills/ocr-transcription/scripts/validate_chunk.py --all

# دمج الأجزاء وتنظيف الملفات المؤقتة
python .agents/skills/ocr-transcription/scripts/merge_parts.py
```

### ٣. التحويل إلى Word
لتحويل ملف Markdown المدمج إلى مستند Word:
```bash
python .agents/skills/ocr-transcription/scripts/convert_to_docx.py "md/input.md" "word/output.docx"
```

---

## التفاصيل البرمجية

- **[splitt_pdf.py](.agents/skills/ocr-transcription/scripts/splitt_pdf.py)**: يحلل نطاقات الصفحات عبر `parse_pages()` ويجمعها متصلة بـ `group_contiguous()` وينشئ ملفات الأجزاء.
- **[convert_to_docx.py](.agents/skills/ocr-transcription/scripts/convert_to_docx.py)**: يستدعي Pandoc ويحقن معاملات اللغة ويتحقق من تثبيت الأداة محلياً.
- **[merge_parts.py](.agents/skills/ocr-transcription/scripts/merge_parts.py)**: يدمج ملفات الأجزاء في ملف واحد ويعالج فحص التكرار.
- **[transcription_rules.md](.agents/skills/ocr-transcription/references/transcription_rules.md)**: يضبط قواعد النسخ وتخريج التلف والصفحات الفارغة ومناقلة الملفات.
- **[execution_rules.md](.agents/skills/ocr-transcription/references/execution_rules.md)**: يحدد قواعد تجميد العمل عند الخطأ البرمجي وإجراءات التعافي.
