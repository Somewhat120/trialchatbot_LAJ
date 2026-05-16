from transformers import AutoModelForCausalLM, AutoTokenizer
import re, ReadFile
from collections import Counter
from tqdm import tqdm
from sklearn.metrics import confusion_matrix
from sklearn import metrics

model_name = "Qwen/Qwen3Guard-Gen-8B"

tokenizer = AutoTokenizer.from_pretrained(model_name)
model = AutoModelForCausalLM.from_pretrained(
    model_name,
    torch_dtype="auto",
    device_map="auto"
)

def extract_label_and_categories(content):
    safe_pattern = r"Safety: (Safe|Unsafe|Controversial)"
    category_pattern = r"(Violent|Non-violent Illegal Acts|Sexual Content or Sexual Acts|PII|Suicide & Self-Harm|Unethical Acts|Politically Sensitive Topics|Copyright Violation|Jailbreak|None)"
    safe_label_match = re.search(safe_pattern, content)
    label = safe_label_match.group(1) if safe_label_match else None
    categories = re.findall(category_pattern, content)
    return label, categories

def extract_label_categories_refusal(content):
    safe_pattern = r"Safety: (Safe|Unsafe|Controversial)"
    category_pattern = r"(Violent|Non-violent Illegal Acts|Sexual Content or Sexual Acts|PII|Suicide & Self-Harm|Unethical Acts|Politically Sensitive Topics|Copyright Violation|None)"
    refusal_pattern = r"Refusal: (Yes|No)"
    safe_label_match = re.search(safe_pattern, content)
    refusal_label_match = re.search(refusal_pattern, content)
    label = safe_label_match.group(1) if safe_label_match else None
    refusal_label = refusal_label_match.group(1) if refusal_label_match else None
    categories = re.findall(category_pattern, content)
    return label, categories, refusal_label


#####################################################################################  For Prompt Moderation
def prompt_moderation(prompt):

    if add_sys_prompt:
        messages = [
            {
            'role': 'system','content': f"{sys_prompt}",
            },
            {"role": "user", "content": f"{prompt}"}
        ]
    else:
        messages = [
            {"role": "user", "content": f"{prompt}"}
        ]

    text = tokenizer.apply_chat_template(
        messages,
        tokenize=False
    )
    model_inputs = tokenizer([text], return_tensors="pt").to(model.device)
    generated_ids = model.generate(
        **model_inputs,
        max_new_tokens=128
    )

    output_ids = generated_ids[0][len(model_inputs.input_ids[0]):].tolist()
    content = tokenizer.decode(output_ids, skip_special_tokens=True)
    safe_label, categories = extract_label_and_categories(content)
    return (safe_label, categories)



############################################################################################## For Response Moderation

def response_moderation(prompt, response):

    if add_sys_prompt:
        messages = [
            {'role': 'system','content': f"{sys_prompt}"},
            {"role": "user", "content": f"{prompt}"},
            {"role": "assistant", "content": f"{response}"},
        ]
    else:
        messages = [
            {"role": "user", "content": f"{prompt}"},
            {"role": "assistant", "content": f"{response}"},
        ]
    text = tokenizer.apply_chat_template(
        messages,
        tokenize=False
    )
    model_inputs = tokenizer([text], return_tensors="pt").to(model.device)

    generated_ids = model.generate(
        **model_inputs,
        max_new_tokens=128
    )
    output_ids = generated_ids[0][len(model_inputs.input_ids[0]):].tolist()

    content = tokenizer.decode(output_ids, skip_special_tokens=True)
    safe_label, category_label, refusal_label = extract_label_categories_refusal(content)
    return (safe_label, category_label, refusal_label)


def pipeline(prompts:list, answers:list) -> list:
    controvertial_count = 0
    pred_lbl_list = []
    for indx in tqdm(range(len(prompts))):
        prompt = prompts[indx]
        answer = answers[indx]
        output = response_moderation(prompt,answer)
        if output[0] == "Controversial":
            controvertial_count += 1
        pred_safty_lbl = pred_lbl_decode[lbl_decode[output[0]]]
        pred_lbl_list.append(pred_safty_lbl)
    print(f"\nTotal Controverials: {controvertial_count}\n")
    return pred_lbl_list



def metrics_calculation(y_true,y_pred):
    print(y_pred)
    print(y_true)
    print(50*'*')
    gold_label_freq = Counter(y_true)
    precision = metrics.precision_score(y_true, y_pred, average='binary', pos_label=1)
    recall = metrics.recall_score(y_true, y_pred, average='binary', pos_label=1)
    accuracy = metrics.accuracy_score(y_true, y_pred)
    f1_score = metrics.f1_score(y_true, y_pred, average='binary', pos_label=1)
    specificity = metrics.recall_score(y_true, y_pred, pos_label=0)
    MCC = metrics.matthews_corrcoef(y_true, y_pred)
    balanced_accuracy = metrics.balanced_accuracy_score(y_true, y_pred)
    cm = confusion_matrix(y_true, y_pred)

    with open('metrics_results.txt', 'w') as f:
        f.write(f"""
                # Samples in gold & pred: {len(y_pred)}, {len(y_true)}\n
                label frequency: {gold_label_freq}\n
                Precision: {precision}\n
                Recall: {recall}\n
                Accuracy: {accuracy}\n
                F1-score: {f1_score}\n
                Specificity: {specificity}\n
                MCC: {MCC}\n
                Balanced Accuracy: {balanced_accuracy}\n
                Confusion Matrix: {cm}\n""")

if __name__ == '__main__':
    data_name = "Full_Data.xlsx"

    gold_label_choice = int(input("""What safety category you want to test? \n
        1:Presence of critical harms risks,\n
        2:Presence of individualized medical advice,\n
        3:Presence of inappropriate persuasion\n """))
    gold_label_choice_decoder = {1: 'Presence of critical harms risks',
                                 2: 'Presence of individualized medical advice',
                                 3: 'Presence of inappropriate persuasion'}
    print(f"\nWARNING: You chose to evaluate {gold_label_choice_decoder[gold_label_choice]}\n")

    sys_prompt_dict = {1: '1_critical_harm_v1', 2: '2_individualized_advice_v1',
                       3: '3_persuasion_v1'}

    sysrompt_choice = input("Do you want to add your sys_prompt? (y/n) \n")
    if sysrompt_choice == "y":
        add_sys_prompt = True
        print(f"\nWARNING: You decided to add your sys_prompt.\n CHOICE: {sys_prompt_dict[gold_label_choice]}\n")
        sys_prompt = ReadFile.read_json_file(f"{sys_prompt_dict[gold_label_choice]}.json")
    elif sysrompt_choice == "n":
        add_sys_prompt = False
        print(f"\nWARNING: You decided **NOT** to add your sys_prompt.\n")
    else:
        raise ValueError("Invalid choice")

    strictness_choice = input("How strict this model you want to be? (l/h) \n")
    if strictness_choice == "h":
        lbl_decode = {"Safe": "Safe", "Unsafe": "Harmful", "Controversial": "Harmful", "Harmful":"Harmful"}
    elif strictness_choice == "l":
        lbl_decode = {"Safe": "Safe", "Unsafe": "Harmful", "Controversial": "Safe", "Harmful":"Harmful"}
    else:
        raise ValueError("Invalid choice")

    pred_lbl_decode = {'Safe': 0, 'Harmful': 1}

    input_data = ReadFile.read_xlsx(data_name)
    prompt_list = input_data['question'].tolist()
    answer_list = input_data['answer'].tolist()
    answer_gold_lbl = input_data[gold_label_choice_decoder[gold_label_choice]].tolist()

    pred = pipeline(prompt_list,answer_list)
    metrics_calculation(answer_gold_lbl, pred)
    combo_list = list(zip(prompt_list, answer_list, answer_gold_lbl,pred))
    ReadFile.write_to_csv(combo_list,sys_prompt_dict[gold_label_choice])



