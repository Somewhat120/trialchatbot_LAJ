import pandas as pd
import json

def read_csv(csv_filename: str) -> pd.DataFrame:
    csv_doc = pd.read_csv(csv_filename)
    return csv_doc

def read_xlsx(excel_filename: str) -> pd.DataFrame:
    xlsx_dataframe = pd.read_excel(excel_filename)
    return xlsx_dataframe

def read_txt(txt_filename:str) -> str:
    with open(txt_filename, 'r') as form:
        consent_text = form.read()
    return consent_text


def read_json_file(settings_json_file):
    with open(settings_json_file, 'r') as file:
        data = json.load(file)
        sys_prompt = data["system_role"]
        user_prompt = data["instruction_template"]
        instruction = sys_prompt + '\n' + user_prompt
    return instruction


def write_to_csv(outputlist: list[tuple[str,str,str,str]], name) -> None: # prompt_list,answer_list,gold_label_list, pred_label_list
    final_text = pd.DataFrame(outputlist,columns=['Prompt','Answer','Gold_answer_label','Predicted_answer_label'])
    final_text.to_csv(f"Result_{name}.csv", index=True)


def log_error_txt(error_log):
    with open("Error_Log.txt", "a", encoding="utf-8") as file:
        for entry in error_log:
            file.write(entry + "\n")

def cm_log(cm):
    with open(f"CM.txt", "a", encoding="utf-8") as file:
        file.write(f"{cm}\n")