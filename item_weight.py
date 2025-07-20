import pandas as pd

def get_item_weight(item_name, excel_path='items.xlsx'):
    """
    Returns the weight of a given item from an Excel sheet.

    Parameters:
    - item_name (str): The name of the item to look up.
    - excel_path (str): Path to the Excel file (default is 'items.xlsx').

    Returns:
    - float: The weight of the item if found.
    - None: If the item is not found.
    """
    try:
        df = pd.read_excel(excel_path)

        # Normalize column names
        df.columns = df.columns.str.strip()

        # Find matching item (case-insensitive)
        match = df[df['Item'].str.lower() == item_name.lower()]

        if not match.empty:
            return match.iloc[0]['Weight']
        else:
            print(f"Item '{item_name}' not found in the Excel sheet.")
            return None

    except FileNotFoundError:
        print(f"Excel file '{excel_path}' not found.")
        return None
    except Exception as e:
        print(f"Error reading Excel file: {e}")
        return None
