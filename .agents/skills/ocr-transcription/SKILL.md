---
name: ocr-transcription
description: >
  Orchestrates end-to-end OCR text extraction from PDF books and documents.
  Splits large PDFs, dispatches subagents for parallel transcription using
  view_file (no external OCR libraries), validates structural integrity,
  and merges final Markdown output. Use when user asks to "استخلص نص هذا كتاب",
  "حوّل هذا PDF إلى نص", "نسّخ هذا المستند", "استخرج النص من هذا الملف",
  "extract text from PDF", "OCR this book", "transcribe this document",
  "معالجة كتاب", "تفريغ كتاب", or any request involving extracting, transcribing,
  or digitizing text from PDF files.
---

# مهارة استخلاص النصوص وتنسيق الأجزاء (OCR Transcription Skill)

- **المرساة المفاهيمية (Leading Word)**: الـ **chunk** (جمعها **chunks**) هي الوحدة الأساسية للتقسيم والمعالجة (20 صفحة كحد أقصى).
- **المرجعيات التفصيلية**:
  - قواعد النسخ والأمانة العلمية وحظر المكتبات الخارجية: **[references/transcription_rules.md](references/transcription_rules.md)**
  - بروتوكولات الفشل والتعافي والتحقق الفردي: **[references/execution_rules.md](references/execution_rules.md)**

---

## ١. استراتيجية المعالجة وإدارة الوكلاء (Orchestration & Processing)

- **الوكيل الرئيسي**: يدير سير العمل الإجرائي، ويتحقق من صفحات الملف للتفريع، ويستخلص الملفات الصغيرة مباشرة، ويطلق الوكلاء الفرعيين للملفات الكبيرة ويجري دمجها.
- **تعدد الملفات**: عند وجود عدة ملفات PDF في مجلد المصدر، يستشير المستخدم تفاعلياً لتحديد طريقة المعالجة (ملف واحد أم بالتتابع).
- **حظر الاستخلاص الخارجي**: يُحظر استخدام أي أدوات خارجية أو مكتبات OCR؛ القراءة حصرية عبر أداة `view_file` للنموذج (انظر تفصيل الحظر في `transcription_rules.md`).
- **حجم الـ chunk الثابت**: 20 صفحة كحد أقصى لكل **chunk**.
- **المخرجات الافتراضية**: تُكتب مخرجات الدمج النهائي والتحويل إلى المجلد النشط الحالي `.` ما لم يحدد مسار فرعي مخصص (يتم إنشاؤه تلقائياً).

---

## ٢. بروتوكول التنفيذ التتابعي (Sequential Execution Pipeline)

### الخطوة الأولى: التحقق من عدد الصفحات والتفريع
1. يقرأ الوكيل الرئيسي عدد صفحات ملف الـ PDF أولاً بتشغيل السكربت بالمعامل `--info_only`:
   ```bash
   python .agents/skills/ocr-transcription/scripts/splitt_pdf.py "pdf/input.pdf" --info_only
   ```
2. **التفريع الإلزامي**:
   - **إذا كان عدد الصفحات 20 صفحة أو أقل**: يسلك الوكيل **المسار السريع (Fast Track)** الموثق في القسم (٣).
   - **إذا كان عدد الصفحات أكثر من 20 صفحة**: يسلك الوكيل **المسار القياسي المجزأ (Chunked Pipeline)** الموثق في القسم (٤).

---

## ٣. المسار السريع (Fast Track) - للملفات الصغيرة (<= 20 صفحة)

1. **الاستخلاص المباشر**: يقوم الوكيل الرئيسي بقراءة صفحات ملف PDF مباشرة صفحة صفحة باستخدام أداة `view_file`.
2. **الكتابة والتطبيق**: يكتب الوكيل النص المستخلص مباشرة في ملف Markdown نهائي في الجذر بالاسم المطابق لملف الـ PDF الأصلي (مثال: `pdf/Assignment 1.pdf` ينتج `Assignment 1.md` في مجلد الجذر)، مع الالتزام التام بقواعد النسخ وفواصل الصفحات `--- Page [Number] ---` الموثقة في `references/transcription_rules.md`.
3. **التحويل المباشر لـ Word**: فور الانتهاء، يشغل الوكيل سكربت التحويل لملف Word تلقائياً:
   ```bash
   python .agents/skills/ocr-transcription/scripts/convert_to_docx.py "filename.md" "filename.docx"
   ```

---

## ٤. المسار القياسي المجزأ (Chunked Pipeline) - للملفات الكبيرة (> 20 صفحة)

### الخطوة الأولى: التهيئة والتقسيم
1. تشغيل أداة التقسيم والتهيئة للملف النشط:
   ```bash
   python .agents/skills/ocr-transcription/scripts/splitt_pdf.py "pdf/input.pdf"
   ```
2. قراءة ملف المتابعة `output_parts/progress.json` لمعرفة نطاق الـ **chunks** والصفحات المطلوبة.

### الخطوة الثانية: استخلاص الـ chunks
1. يحدد الوكيل الرئيسي الـ **chunks** ذات الحالة `"pending"` في ملف المتابعة، ويستبعد أي **chunk** له ملف مؤقت `output_parts/part_N_temp.md` متواجد على القرص تفادياً للتكرار.
2. يُطلق الوكلاء الفرعيون من نوع `"self"` بالتوازي (بحد أقصى 10 وكلاء في الدفعة الواحدة) لاستخلاص النصوص.
3. **معيار الإتمام**: التأكد من إتمام كافة الوكلاء الفرعيين لمهامهم وعودتهم بتقرير النجاح في المحادثة.

### الخطوة الثالثة: التحقق المؤجل (Deferred Validation)
1. بعد اكتمال عمل كافة الوكلاء الفرعيين وولادة الملفات المؤقتة، يُشغل الوكيل الرئيسي سكربت التحقق:
   ```bash
   python .agents/skills/ocr-transcription/scripts/validate_chunk.py --all
   ```
2. يُحدّث السكربت حالة الـ **chunks** تلقائياً في `progress.json` إلى `"completed"` في حال النجاح مع توليد ملف المخرجات المعتمد، أو `"failed"` في حال الإخفاق.

### الخطوة الرابعة: التعافي من الأخطاء (Error Recovery)
- إذا فشل أي **chunk** في التحقق (حالة `"failed"`):
  1. الالتزام الصارم بـ **[references/execution_rules.md](references/execution_rules.md)**.
  2. حذف الملف المؤقت الفاشل وإعادة الاستخلاص والتحقق الفردي له قبل الانتقال للخطوة التالية.

### الخطوة الخامسة: الدمج والتدقيق والتحويل إلى Word
1. عند اكتمال كافة الأجزاء ووسمها بـ `"completed"`، يُشغل الوكيل الرئيسي سكربت الدمج:
   ```bash
   python .agents/skills/ocr-transcription/scripts/merge_parts.py
   ```
2. إذا اجتاز المستند فحص التكرار (Duplication Check) بنجاح، يُتم السكربت تنظيف وحذف الملفات الوسيطة وملف المتابعة، ويُسلم المستند النهائي في المجلد الحالي.
3. تشغيل سكربت التحويل لملف Word تلقائياً:
   ```bash
   python .agents/skills/ocr-transcription/scripts/convert_to_docx.py "filename.md" "filename.docx"
   ```
   *(يقوم السكربت بإنشاء مجلد المخرجات تلقائياً في حال إدراجه بالمسار).*
