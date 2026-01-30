import sqlite3
import os

print("=" * 60)
print("PDF INTEGRATION VERIFICATION TEST")
print("=" * 60)

# Connect to the database
conn = sqlite3.connect('backend/data/chemicals.db')
cursor = conn.cursor()

# 1. Check if PDF_Folder exists
pdf_folder_exists = os.path.exists('PDF_Folder')
pdf_material_exists = os.path.exists('PDF_Folder/Material')
pdf_guides_exists = os.path.exists('PDF_Folder/Guides')

print(f"\n📁 PDF Folder Structure:")
print(f"   PDF_Folder/: {'✅ EXISTS' if pdf_folder_exists else '❌ MISSING'}")
print(f"   PDF_Folder/Material/: {'✅ EXISTS' if pdf_material_exists else '❌ MISSING'}")
print(f"   PDF_Folder/Guides/: {'✅ EXISTS' if pdf_guides_exists else '❌ MISSING'}")

if pdf_material_exists:
    pdf_count = len([f for f in os.listdir('PDF_Folder/Material') if f.endswith('.pdf')])
    print(f"   Total PDFs in Material/: {pdf_count}")

# 2. Check database for chemicals with CHRIS codes (linked to PDFs)
cursor.execute("SELECT count(*) FROM chemicals WHERE chris_codes IS NOT NULL AND chris_codes != ''")
chris_count = cursor.fetchone()[0]

# 3. Check total chemicals in database
cursor.execute("SELECT count(*) FROM chemicals")
total_count = cursor.fetchone()[0]

# 4. Check specific chemical: ACETONE
cursor.execute("SELECT name, chris_codes, nfpa_health, nfpa_flam, nfpa_react, nfpa_special FROM chemicals WHERE name = 'ACETONE'")
acetone_data = cursor.fetchone()

print(f"\n📊 Database Statistics:")
print(f"   Total Chemicals: {total_count}")
print(f"   Chemicals with CHRIS Codes: {chris_count}")
print(f"   Coverage: {(chris_count/total_count*100):.1f}%")

print(f"\n🧪 ACETONE Test Case:")
if acetone_data:
    print(f"   Name: {acetone_data[0]}")
    print(f"   CHRIS Code: {acetone_data[1] if acetone_data[1] else 'N/A'}")
    print(f"   NFPA: H={acetone_data[2]}, F={acetone_data[3]}, R={acetone_data[4]}, S={acetone_data[5]}")
    
    # Check if corresponding PDF exists
    if acetone_data[1]:
        chris_code = acetone_data[1].strip()
        pdf_path = f"PDF_Folder/Material/{chris_code}.pdf"
        pdf_exists = os.path.exists(pdf_path)
        print(f"   PDF File: {pdf_path}")
        print(f"   PDF Status: {'✅ EXISTS' if pdf_exists else '❌ MISSING'}")
        
        if pdf_exists:
            print(f"\n✅ VERIFICATION PASSED: PDF integration is working!")
        else:
            print(f"\n⚠️  VERIFICATION WARNING: Database has CHRIS code but PDF missing")
    else:
        print(f"   ⚠️  No CHRIS code found for ACETONE")
else:
    print(f"   ❌ ACETONE not found in database")

# 5. Sample 5 random chemicals with CHRIS codes
cursor.execute("SELECT name, chris_codes FROM chemicals WHERE chris_codes IS NOT NULL AND chris_codes != '' LIMIT 5")
samples = cursor.fetchall()

print(f"\n📋 Sample Chemicals with PDF Links:")
for name, chris_code in samples:
    pdf_path = f"PDF_Folder/Material/{chris_code.strip()}.pdf"
    status = "✅" if os.path.exists(pdf_path) else "❌"
    print(f"   {status} {name} → {chris_code}.pdf")

conn.close()

print("\n" + "=" * 60)
print("VERIFICATION COMPLETE")
print("=" * 60)
