import pandas as pd
from datetime import datetime, timedelta
import random

# Set random seed for reproducibility
random.seed(42)

# Generate test data
num_rows = 500  # Good size for testing - will create multiple files if chunk size is small

data = {
    'ID': [f'PR-{i:05d}' for i in range(1, num_rows + 1)],
    'Vendor_Name': [random.choice(['Acme Corp', 'Tech Solutions Inc', 'Global Supplies', 'Best Services LLC', 'Prime Materials Co']) for _ in range(num_rows)],
    'Invoice_Number': [f'INV-{random.randint(1000, 9999)}-{random.randint(100, 999)}' for _ in range(num_rows)],
    'Invoice_Date': [(datetime(2024, 1, 1) + timedelta(days=random.randint(0, 365))).strftime('%Y-%m-%d') for _ in range(num_rows)],
    'Amount': [round(random.uniform(100.00, 50000.00), 2) for _ in range(num_rows)],
    'GL_Account': [f'{random.randint(1000, 9999)}-{random.randint(100, 999)}' for _ in range(num_rows)],
    'Department': [random.choice(['IT', 'Finance', 'Operations', 'HR', 'Sales', 'Marketing']) for _ in range(num_rows)],
    'Status': [random.choice(['Pending', 'Approved', 'Paid', 'Rejected']) for _ in range(num_rows)],
    'Description': [f'Purchase order for {random.choice(["office supplies", "software license", "equipment", "consulting services", "maintenance"])}' for _ in range(num_rows)],
    'Quantity': [random.randint(1, 100) for _ in range(num_rows)],
    'Unit_Price': [round(random.uniform(10.00, 1000.00), 2) for _ in range(num_rows)],
    'Tax_Rate': [round(random.uniform(0.05, 0.10), 4) for _ in range(num_rows)],
    'Total_Tax': [0.0] * num_rows,  # Will calculate below
    'Net_Amount': [0.0] * num_rows,  # Will calculate below
    'Approved_By': [random.choice(['John Smith', 'Jane Doe', 'Bob Johnson', 'Alice Williams', None]) for _ in range(num_rows)],
    'Notes': [random.choice(['', 'Urgent', 'Follow up required', 'Contract renewal', None]) for _ in range(num_rows)]
}

# Create DataFrame
df = pd.DataFrame(data)

# Calculate derived fields
df['Total_Tax'] = (df['Amount'] * df['Tax_Rate']).round(2)
df['Net_Amount'] = (df['Amount'] - df['Total_Tax']).round(2)

# Add some intentional duplicates for testing duplicate finder
duplicate_indices = [5, 6, 25, 26, 50, 51, 100, 101, 200, 201, 300, 301, 400, 401]  # Create some duplicate rows
for idx in duplicate_indices[::2]:
    if idx + 1 < len(df):
        df.iloc[idx + 1] = df.iloc[idx].copy()

# Add some missing values strategically
missing_indices = [10, 20, 30, 40, 60, 80, 100, 120, 150, 180, 200, 250, 300, 350, 400, 450]
for idx in missing_indices:
    df.at[idx, 'Approved_By'] = None
    df.at[idx, 'Notes'] = None
    if idx % 2 == 0:
        df.at[idx, 'Tax_Rate'] = None

# Add some edge cases
df.at[0, 'Amount'] = 0.00  # Zero amount
df.at[1, 'Amount'] = 999999.99  # Large amount
df.at[2, 'Description'] = 'Special chars: !@#$%^&*()'  # Special characters
df.at[3, 'Vendor_Name'] = 'Very Long Company Name That Might Cause Display Issues In Some Systems'  # Long text

# Save to Excel with formatting
output_file = 'test_data_dragon.xlsx'
with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
    df.to_excel(writer, sheet_name='Test Data', index=False)
    
    # Get the workbook and worksheet for formatting
    workbook = writer.book
    worksheet = writer.sheets['Test Data']
    
    # Auto-adjust column widths
    for column in worksheet.columns:
        max_length = 0
        column_letter = column[0].column_letter
        for cell in column:
            try:
                if len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except:
                pass
        adjusted_width = min(max_length + 2, 50)
        worksheet.column_dimensions[column_letter].width = adjusted_width

print(f"Test file created: {output_file}")
print(f"   Rows: {len(df)}")
print(f"   Columns: {len(df.columns)}")
print(f"\nFile includes:")
print("   • Multiple data types (text, numbers, dates, currency)")
print("   • Intentional duplicates (rows 5-6, 25-26, 50-51, 100-101, 200-201, 300-301, 400-401)")
print("   • Missing values (NULL/None in various columns)")
print("   • Edge cases (zero values, large numbers, special chars)")
print("   • Realistic ERP-style data (invoices, GL accounts, vendors)")
print("\nPerfect for testing:")
print("   • Excel File Splitter (split into chunks)")
print("   • Column Analyzer (statistics, missing values)")
print("   • Duplicate Finder (find duplicate rows)")
print("   • Data Comparison (compare with modified version)")
print("   • Data Merge (join with another file)")
print("   • Data Validation (validate amounts, dates, etc.)")

