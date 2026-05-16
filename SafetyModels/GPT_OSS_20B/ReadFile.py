import pandas as pd
import json


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
    """Read generation and other parameters"""
    with open(settings_json_file, 'r') as file:
        data = json.load(file)
    return data


# def write_to_csv(outputlist: list[tuple[str,str,str,str,str]]) -> None: # prompt_list,prompt_act_category,answer_list,gold_label_list, pred_label_list
#     final_text = pd.DataFrame(outputlist,columns=['Prompt','Prompt_act_category','Answer','Gold_answer_label','Predicted_answer_label'])
#     final_text.to_csv("3P_AG.csv", index=True)

def write_to_csv(outputlist, range_name, name) -> None: # prompt_list,answer_list,gold_label_list, pred_label_list
    final_text = pd.DataFrame(outputlist,columns=['Prompt','Answer','Gold_answer_label','Predicted_answer_label','CoT'])
    final_text.to_csv(f"{name}-{range_name}.csv", index=True)


def log_error_txt(error_log):
    with open("Error_Log.txt", "a", encoding="utf-8") as file:
        for entry in error_log:
            file.write(entry + "\n")

def cm_log(cm):
    with open(f"CM.txt", "a", encoding="utf-8") as file:
        file.write(f"{cm}\n")

# def data_standardization(text:DataFrame, val:int) -> list[str]:
#     atomic_list = []
#     dict_counter = defaultdict(int)
#     for indx in range(len(text)):
#         prompt = text.iloc[indx]['Prompt']
#         prompt_act_category = text.iloc[indx]['Prompt_act_category']
#         prompt_label = text.iloc[indx]['Prompt_label']
#         answer = text.iloc[indx]['Answers']
#         answer_label = text.iloc[indx]['Answers_label']
#         if dict_counter[answer_label]<= val:
#             dict_counter[answer_label] += 1
#             atomic_list.append([prompt,prompt_act_category ,prompt_label, answer, answer_label])
#     return atomic_list


# if __name__ == '__main__':
#     a_lal = read_xlsx(r'D:\LoyolaUniversityChicago\Semester1\IndependentResearch\Resources\Code\LLM_Classifier\A_Classifier\Dataset\Mixed\Test\Test.xlsx')
#     a_lal = data_standardization(a_lal,10)
#     print(a_lal[0])
#     for i in range(len(a_lal)):
#         prompt = a_lal[i][0]
#         prompt_label = a_lal[i][1]
#         answer = a_lal[i][2]
#         answer_label = a_lal[i][3]
#         print(f"prompt: {prompt}")
#         print(f"prompt label: {prompt_label}")
#         print(f"answer: {answer}")
#         print(f"answer label: {answer_label}")
#         print("+++++++++++++++++++++++++")
