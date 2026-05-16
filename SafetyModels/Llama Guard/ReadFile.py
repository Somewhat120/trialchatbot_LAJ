import pandas as pd
import json
from collections import defaultdict
from pandas import DataFrame

def read_csv(csv_filename: str) -> pd.DataFrame:
    csv_doc = pd.read_csv(csv_filename)
    return csv_doc

def read_xlsx(excel_filename: str) -> pd.DataFrame:
    text = pd.read_excel(excel_filename)
    return text

def read_txt(txt_filename:str) -> str:
    with open(txt_filename, 'r') as form:
        consent_text = form.read()
    return consent_text


def read_json_file(settings_json_file):
    with open(settings_json_file, 'r') as file:
        data = json.load(file)
    return data



def write_to_csv(outputlist: list[tuple[str,str,str,str]], name) -> None:
    final_text = pd.DataFrame(outputlist,columns=['Prompt','Answer','Gold_answer_label','Predicted_answer_label'])
    final_text.to_csv(f"Resul{name}.csv", index=True)



# if __name__ == '__main__':
#     one, two = read_json_file('1_critical_harm_v1.json')
#     user_input = two.replace("{question}", ')))))))))))))))))))))))').replace("{answer}",'**********************')
#     print(one)