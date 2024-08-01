import json
import logging
import time
import os
import json

import pandas as pd
from genai.schema import ChatRole

from conversational_prompt_engineering.backend.prompt_building_util import build_few_shot_prompt, LLAMA_END_OF_MESSAGE, \
    _get_llama_header, LLAMA_START_OF_INPUT
from conversational_prompt_engineering.backend.output_dir_mapping import output_dir_hash_to_name

from conversational_prompt_engineering.util.bam import BamGenerate
from conversational_prompt_engineering.util.watsonx import WatsonXGenerate


def extract_delimited_text(txt, delims):
    try:
        if type(delims) is str:
            delims = [delims]
        for delim in delims:
            if delim in txt:
                begin = txt.index(delim) + len(delim)
                end = begin + txt[begin:].index(delim)
                return txt[begin:end]
        return txt  # delims not found in text
    except ValueError:
        return txt


def create_model_client(credentials, model_name, api):
    with open(os.path.join(os.path.dirname(__file__),"model_params.json"), "r") as f:
        params = json.load(f)
    model_params = {x: y for x, y in params['models'][model_name].items()}
    model_params.update({'api_key' if x == 'key' else x: y for x, y in credentials.items()})
    model_params['api_endpoint'] = params[f'{api}_api_endpoint']

    if api == "watsonx":
        return WatsonXGenerate(model_params)
    elif api == "bam":
        return BamGenerate(model_params)
    else:
        raise ValueError(f'Invalid api: {api}. Should be either watsonx or bam')


def format_chat(chat, model_id):
    if any([name in model_id for name in ['mixtral', 'prometheus']]):
        bos_token = '<s>'
        eos_token = '</s>'
        chat_for_mixtral = []
        prev_role = None
        for m in chat:
            if m["role"] == prev_role:
                chat_for_mixtral[-1]["content"] += "\n" + m["content"]
            else:
                chat_for_mixtral.append(m)
            prev_role = m["role"]

        for m in chat_for_mixtral:
            if m["role"] == 'user':
                m["content"] = 'user: ' + m["content"]
            elif m["role"] == 'system':
                m["role"] = 'user'
                m["content"] = 'system: ' + m["content"]

        prompt = bos_token
        for m in chat_for_mixtral:
            if m['role'] == 'user':
                prompt += '[INST] ' + m['content'] + ' [/INST] '
            else:
                prompt += m['content'] + eos_token + ' '
        return prompt
    elif 'llama' in model_id:
        msg_str = LLAMA_START_OF_INPUT
        for m in chat:
            msg_str += _get_llama_header(m['role']) + "\n\n" + m['content'] + LLAMA_END_OF_MESSAGE
        msg_str += _get_llama_header(ChatRole.ASSISTANT)
        return msg_str
    else:
        raise ValueError(f"model {model_id} not supported")


class ChatManagerBase:
    def __init__(self, credentials, model, conv_id, target_model, api, email_address, output_dir, config_name) -> None:
        logging.info(f"selected {model}")
        logging.info(f"conv id: {conv_id}")
        logging.info(f"credentials from environment variables: {credentials}")
        logging.info(f"user email address: {email_address}")

        self.bam_client = create_model_client(credentials, model, api)
        self.target_bam_client = create_model_client(credentials, target_model, api)
        self.conv_id = conv_id
        self.dataset_name = None
        self.state = None
        self.timing_report = []
        self.email_address = email_address
        self.out_dir = output_dir
        self.config_name = config_name
        logging.info(f"output is saved to {os.path.abspath(self.out_dir)}")


    def save_prompts_and_config(self, approved_prompts, approved_outputs):
        chat_dir = os.path.join(self.out_dir, "chat")
        os.makedirs(chat_dir, exist_ok=True)
        with open(os.path.join(chat_dir, "prompts.json"), "w") as f:
            for p in approved_prompts:
                p['prompt_with_format'] = build_few_shot_prompt(p['prompt'], [],
                                                                self.target_bam_client.parameters['model_id'])
                p['prompt_with_format_and_few_shots'] = build_few_shot_prompt(p['prompt'], approved_outputs,
                                                                              self.target_bam_client.parameters[
                                                                                  'model_id'])
            json.dump(approved_prompts, f)
        with open(os.path.join(chat_dir, "config.json"), "w") as f:
            json.dump({"model": self.bam_client.parameters['model_id'], "dataset": self.dataset_name,
                       "example_num": self.example_num, "model_chat_length": self.model_chat_length,
                       "user_chat_length": self.user_chat_length}, f)

    def save_chat_html(self, chat, file_name):
        def _format(msg):
            role = msg['role'].upper()
            txt = msg['content']
            relevant_tags = {k: msg[k] for k in (msg.keys() - {'role', 'content', 'tooltip'})}
            tags = ""
            if relevant_tags:
                tags = str(relevant_tags)
            return f"<p><b>{role}: </b>{txt} {tags}</p>".replace("\n", "<br>")

        chat_dir = os.path.join(self.out_dir, "chat")
        os.makedirs(chat_dir, exist_ok=True)
        df = pd.DataFrame(chat)
        df.to_csv(os.path.join(chat_dir, f"{file_name.split('.')[0]}.csv"), index=False)
        with open(os.path.join(chat_dir, file_name), "w") as html_out:
            content = "\n".join([_format(x) for x in chat])
            header = "<h1>IBM Research Conversational Prompt Engineering</h1>"
            html_template = f'<!DOCTYPE html><html>\n<head>\n<title>CPE</title>\n</head>\n<body style="font-size:20px;">{header}\n{content}\n</body>\n</html>'
            html_out.write(html_template)

    def _add_msg(self, chat, role, msg):
        chat.append({'role': role, 'content': msg})

    def print_timing_report(self):
        df = pd.DataFrame(self.timing_report)
        logging.info(df)
        logging.info(f"Average processing time: {df['total_time'].mean()}")
        self.timing_report = sorted(self.timing_report, key=lambda row: row['total_time'])
        logging.info(f"Highest processing time: {self.timing_report[-1]}")
        logging.info(f"Lowest processing time: {self.timing_report[0]}")

    def _generate_output_and_log_stats(self, conversation, client, max_new_tokens=None):
        start_time = time
        generated_texts, stats_dict = client.send_messages(conversation, max_new_tokens)
        elapsed_time = time.time() - start_time.time()
        timing_dict = {"total_time": elapsed_time, "start_time": start_time.strftime("%d-%m-%Y %H:%M:%S")}
        timing_dict.update(stats_dict)
        logging.info(timing_dict)
        self.timing_report.append(timing_dict)
        return generated_texts

    def _generate_output(self, prompt_str):
        generated_texts = self._generate_output_and_log_stats(prompt_str, client=self.target_bam_client)
        agent_response = generated_texts[0]
        logging.info(f"got response from model: {agent_response}")
        return agent_response.strip()

    def _get_assistant_response(self, chat, max_new_tokens=None):
        conversation = format_chat(chat, self.bam_client.parameters['model_id'])
        generated_texts = self._generate_output_and_log_stats(conversation, client=self.bam_client,
                                                              max_new_tokens=max_new_tokens)
        agent_response = ''
        for txt in generated_texts:
            if any([f'<|{r}|>' in txt for r in [ChatRole.SYSTEM, ChatRole.USER]]):
                agent_response += txt[: txt.index('<|')]
                break
            agent_response += txt
        logging.info(f"got response from model: {agent_response}")
        return agent_response.strip()
