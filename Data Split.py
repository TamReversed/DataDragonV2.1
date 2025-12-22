import pandas as pd
import os

def split_excel_file(input_file_path, output_folder, chunk_size=40000, base_filename=None):
    """
    Splits an Excel file into multiple files with up to `chunk_size` records each.
    
    Parameters:
        input_file_path (str): The path to the input Excel file.
        output_folder (str): The directory where the output files will be saved.
        chunk_size (int): Number of records per output file. Default is 40000.
        base_filename (str): Optional custom base name for output files.
                           Files will be named: [base_filename]_1of10.xlsx, [base_filename]_2of10.xlsx, etc.
    """
    # Ensure the output directory exists
    os.makedirs(output_folder, exist_ok=True)
    
    # Read the data from the Excel file
    df = pd.read_excel(input_file_path)
    total_rows = len(df)
    num_splits = (total_rows + chunk_size - 1) // chunk_size
    
    # Use custom base filename if provided, otherwise use default
    if not base_filename or base_filename.strip() == '':
        base_filename = "split"

    for i in range(num_splits):
        start_idx = i * chunk_size
        end_idx = min(start_idx + chunk_size, total_rows)
        chunk_df = df.iloc[start_idx:end_idx]
        
        # Create filename in format: [base_filename]_1of10.xlsx
        filename = f"{base_filename}_{i+1}of{num_splits}.xlsx"
        output_path = os.path.join(output_folder, filename)
        
        chunk_df.to_excel(output_path, index=False)
        print(f"Saved {filename} with records {start_idx + 1} to {end_idx}")

# Example usage:
input_file_path = 'PR Amounts Load 1.xlsx'  # File in same directory as script
output_folder = "."  # Current directory (where script is located)
base_filename = "PR_Amounts_Load"  # Optional: Custom base filename for output files

split_excel_file(input_file_path, output_folder, base_filename=base_filename)
