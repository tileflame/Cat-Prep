import fitz
import re
import os
import sqlite3
from PIL import Image

IMAGE_DIR = "images"
PDF_DIR = "pdfs"  
DB_PATH = os.path.join("database", "questions.db")

os.makedirs(IMAGE_DIR, exist_ok=True)
os.makedirs(PDF_DIR, exist_ok=True)
os.makedirs("database", exist_ok=True)

def stitch_images(pixmaps, output_path):
    images = [Image.frombytes("RGB", [p.width, p.height], p.samples) for p in pixmaps]
    total_width = max(img.width for img in images)
    total_height = sum(img.height for img in images)

    stitched_image = Image.new('RGB', (total_width, total_height), (255, 255, 255))
    y_offset = 0
    for img in images:
        stitched_image.paste(img, (0, y_offset))
        y_offset += img.height

    stitched_image.save(output_path)

def parse_and_import_pdf(pdf_path, cursor):
    file_name = os.path.basename(pdf_path)
    print(f"\n📖 Processing PDF: '{file_name}'...")
    
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        print(f"❌ Error opening PDF '{file_name}': {e}")
        return 0

    imported_count = 0
    total_pages = len(doc)

    for page_num in range(total_pages):
        page = doc[page_num]
        text = page.get_text()

        qid_match = re.search(r"Question\s*ID[:\s]+([a-zA-Z0-9\-]+)", text, re.IGNORECASE)
        if not qid_match:
            continue  
        
        qid = qid_match.group(1).strip()
        clean_text = " ".join(text.split())

        # 1. SECTION IDENTIFICATION
        if re.search(r"Test[:\s]+Reading\s+and\s+Writing", clean_text, re.IGNORECASE):
            section = "Reading and Writing"
        elif re.search(r"Test[:\s]+Math", clean_text, re.IGNORECASE):
            section = "Math"
        else:
            if any(w in file_name.lower() for w in ["english", "reading", "writing", "rw"]):
                section = "Reading and Writing"
            elif any(w in file_name.lower() for w in ["math"]):
                section = "Math"
            else:
                section = "Reading and Writing" if "Reading" in clean_text else "Math"

        # 2. DOMAIN IDENTIFICATION
        domain = "General"
        if re.search(r"Algebra", clean_text, re.IGNORECASE): domain = "Algebra"
        elif re.search(r"Advanced\s+Math", clean_text, re.IGNORECASE): domain = "Advanced Math"
        elif re.search(r"Problem[- ]Solving\s+and\s+Data\s+Analysis", clean_text, re.IGNORECASE): domain = "Problem-Solving and Data Analysis"
        elif re.search(r"Geometry\s+and\s+Trigonometry", clean_text, re.IGNORECASE): domain = "Geometry and Trigonometry"
        elif re.search(r"Information\s+and\s+Ideas", clean_text, re.IGNORECASE): domain = "Information and Ideas"
        elif re.search(r"Craft\s+and\s+Structure", clean_text, re.IGNORECASE): domain = "Craft and Structure"
        elif re.search(r"Expression\s+of\s+Ideas", clean_text, re.IGNORECASE): domain = "Expression of Ideas"
        elif re.search(r"Standard\s+English\s+Conventions", clean_text, re.IGNORECASE): domain = "Standard English Conventions"

        # 3. DIFFICULTY & SKILL
        diff_match = re.search(r"Difficulty[:\s]+(Easy|Medium|Hard)", clean_text, re.IGNORECASE)
        if not diff_match:
            diff_match = re.search(r"\b(Easy|Medium|Hard)\b", clean_text)
        difficulty = diff_match.group(1).capitalize() if diff_match else "Medium"

        skill_match = re.search(r"Skill[:\s]+(.*?)(?=Difficulty|$)", clean_text, re.IGNORECASE)
        skill = skill_match.group(1).strip() if skill_match else "General"

        # 4A. QUESTION CROP
        pixmaps = []
        full_question_text = text
        current_p_num = page_num

        while current_p_num < total_pages:
            p = doc[current_p_num]
            p_text = p.get_text()
            rect = p.rect

            if current_p_num > page_num:
                full_question_text += "\n" + p_text

            if current_p_num == page_num:
                q_rects = p.search_for("Question")
                valid_q_rects = [r for r in q_rects if 70 < r.y0 < 350 and r.x0 < 150]
                
                if valid_q_rects:
                    top_crop = max(0.0, valid_q_rects[0].y0 - 5)
                else:
                    diff_rects = [r for r in p.search_for("Difficulty") if r.y1 < 250]
                    top_crop = diff_rects[-1].y1 + 55 if diff_rects else 135.0
            else:
                top_crop = 35.0

            stops = []
            blocks = p.get_text("blocks")
            for b in blocks:
                if len(b) >= 5:
                    block_text = b[4].strip()
                    if block_text.startswith("Correct Answer") or block_text.startswith("Rationale"):
                        if b[1] >= top_crop - 10:
                            stops.append(b[1])
                            
            for word in ["Correct Answer", "Rationale"]:
                for r in p.search_for(word):
                    if r.y0 >= top_crop - 10:
                        stops.append(r.y0)

            if stops:
                bottom_crop = min(stops) - 10
                is_last_page = True
            else:
                bottom_crop = rect.height - 35
                is_last_page = False

            height = bottom_crop - top_crop
            if height > 15:
                crop_box = fitz.Rect(0, top_crop, rect.width, bottom_crop)
                pix = p.get_pixmap(dpi=200, clip=crop_box)
                pixmaps.append(pix)

            if is_last_page or (current_p_num + 1 < total_pages and "Question ID:" in doc[current_p_num + 1].get_text()):
                break

            current_p_num += 1

        if not pixmaps:
            continue

        img_path = os.path.join(IMAGE_DIR, f"{qid}.png")
        if len(pixmaps) == 1:
            pixmaps[0].save(img_path)
        elif len(pixmaps) > 1:
            stitch_images(pixmaps, img_path)

        # 4B. RATIONALE CROP
        rat_pixmaps = []
        rat_p_num = current_p_num
        
        while rat_p_num < total_pages:
            p = doc[rat_p_num]
            rect = p.rect
            
            if rat_p_num == current_p_num:
                rat_stops = []
                blocks = p.get_text("blocks")
                for b in blocks:
                    if len(b) >= 5 and (b[4].strip().startswith("Rationale") or b[4].strip().startswith("Correct Answer")):
                        rat_stops.append(b[1])
                if not rat_stops:
                    for w in ["Correct Answer", "Rationale"]:
                        for r in p.search_for(w):
                            rat_stops.append(r.y0)
                
                rat_top = (min(rat_stops) - 5) if rat_stops else 35.0
            else:
                rat_top = 35.0
                
            rat_bottom = rect.height - 35
            is_rat_last_page = False
            
            next_q_stops = []
            for b in p.get_text("blocks"):
                if len(b) >= 5 and "Question ID" in b[4] and b[1] > rat_top + 20:
                    next_q_stops.append(b[1])
                    
            if next_q_stops:
                rat_bottom = min(next_q_stops) - 5
                is_rat_last_page = True
                
            rat_height = rat_bottom - rat_top
            if rat_height > 15:
                crop_box = fitz.Rect(0, rat_top, rect.width, rat_bottom)
                rat_pixmaps.append(p.get_pixmap(dpi=200, clip=crop_box))
                
            if is_rat_last_page or (rat_p_num + 1 < total_pages and "Question ID:" in doc[rat_p_num + 1].get_text()):
                break
                
            rat_p_num += 1

        rat_img_path = os.path.join(IMAGE_DIR, f"{qid}_rationale.png")
        if len(rat_pixmaps) == 1:
            rat_pixmaps[0].save(rat_img_path)
            db_rationale = rat_img_path
        elif len(rat_pixmaps) > 1:
            stitch_images(rat_pixmaps, rat_img_path)
            db_rationale = rat_img_path
        else:
            db_rationale = "No explanation image generated."

        # 5. ANSWER IDENTIFICATION
        ans_match = re.search(r"Correct Answer[:\s]+([^\n\r]+)", full_question_text, re.IGNORECASE)
        raw_ans = ans_match.group(1).strip() if ans_match else ""
        clean_ans = raw_ans.rstrip('.').strip().upper()

        if clean_ans in ["A", "B", "C", "D"]:
            correct_ans = clean_ans
            is_open_ended = 0
        else:
            correct_ans = raw_ans.rstrip('.').strip()
            is_open_ended = 1

        cursor.execute("""
            INSERT OR REPLACE INTO questions 
            (question_id, section, domain, skill, difficulty, question_img, correct_answer, rationale, is_open_ended)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (qid, section, domain, skill, difficulty, img_path, correct_ans, db_rationale, is_open_ended))

        imported_count += 1

    print(f"  └─ Successfully imported {imported_count} questions from '{file_name}'.")
    return imported_count

def run_importer():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            question_id TEXT PRIMARY KEY,
            section TEXT,
            domain TEXT,
            skill TEXT,
            difficulty TEXT,
            question_img TEXT,
            correct_answer TEXT,
            rationale TEXT,
            is_open_ended INTEGER DEFAULT 0
        )
    """)

    pdf_files = []
    for root, dirs, files in os.walk(PDF_DIR):
        for file in files:
            if file.lower().endswith(".pdf"):
                pdf_files.append(os.path.join(root, file))

    if not pdf_files:
        print(f"⚠️ No PDF files found inside '{PDF_DIR}'! Place your PDFs in the '{PDF_DIR}' directory.")
        conn.close()
        return

    print(f"🔍 Found {len(pdf_files)} PDF file(s) inside '{PDF_DIR}/'. Starting import...")

    total_imported = 0
    for pdf_path in pdf_files:
        total_imported += parse_and_import_pdf(pdf_path, cursor)

    conn.commit()
    conn.close()
    print(f"\n✅ Import Complete! Processed {total_imported} total questions.")

if __name__ == "__main__":
    run_importer()