import json
import logging
import os
import random
import datetime

import pandas as pd
from genai.schema import ChatRole
from conversational_prompt_engineering.backend.prompt_building_util import build_few_shot_prompt


from conversational_prompt_engineering.backend.chat_manager_util import LLAMA_START_OF_INPUT, _get_llama_header, \
    LLAMA_END_OF_MESSAGE, ChatManagerBase, extract_delimited_text

BASELINE_PROMPT = 'Summarize the following text in 2-3 sentences, highlighting the main ideas and key points.'

GRANITE_SYSTEM_MESSAGE = 'You are Granite Chat, an AI language model developed by IBM. You are a cautious assistant. You carefully follow instructions. You are helpful and harmless and you follow ethical guidelines and promote positive behavior. You always respond to greetings (for example, hi, hello, g\'day, morning, afternoon, evening, night, what\'s up, nice to meet you, sup, etc) with "Hello! I am Granite Chat, created by IBM. How can I help you today?". Please do not say anything else and do not start a conversation.'

NUM_USER_EXAMPLES = 3

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')


class ConversationState:
    INITIALIZING = 'initializing'
    INTRODUCTION = 'introduction'
    CONFIRM_CHARACTERISTICS = 'confirm_characteristics'
    CONFIRM_PROMPT = 'confirm_prompt'
    PROCESS_TEXTS = 'process_texts'
    PROCESS_RESPONSES = 'process_responses'
    EVALUATE_PROMPT = 'evaluate_prompt'
    CONFIRM_SUMMARY = 'confirm_summary'
    DONE = 'done'




class DoubleChatManager(ChatManagerBase):
    def __init__(self, bam_api_key, model, conv_id) -> None:
        super().__init__(bam_api_key, model, conv_id)

        self.user_chat = []
        self.hidden_chat = []
        self.text_examples = []

        self.approved_prompts = []
        self.approved_summaries = []
        self.validated_example_idx = 0

        self.user_has_more_texts = True
        self.enable_upload_file = True
        self.out_dir = f'_out/{self.conv_id}/{datetime.datetime.now().strftime("%d-%m-%Y %H:%M:%S")}'
        os.makedirs(self.out_dir, exist_ok=True)

    def _get_assistant_response(self, chat=None, max_new_tokens=None):
        return super()._get_assistant_response(chat or self.hidden_chat, max_new_tokens)

    def _load_admin_params(self):
        with open("backend/admin_params.json", "r") as f:
            params = json.load(f)
        return params

    def add_user_message(self, msg):
        logging.info(f"got input from user: {msg}")
        self._add_msg(self.user_chat, ChatRole.USER, msg)
        self._add_msg(self.hidden_chat, ChatRole.USER, msg)

    def _add_system_msg(self, msg):
        logging.info(f"adding system msg: {msg}")
        self._add_msg(self.hidden_chat, ChatRole.SYSTEM, msg)

    def _add_assistant_msg(self, messages, to_chat):
        chats = {
            'user': [self.user_chat],
            'hidden': [self.hidden_chat],
            'both': [self.user_chat, self.hidden_chat],
        }[to_chat]
        if isinstance(messages, str):
            messages = [messages]

        for msg in messages:
            for chat in chats:
                self._add_msg(chat, ChatRole.ASSISTANT, msg)

    def _init_chats(self):
        if 'granite' in self.bam_client.parameters['model_id']:
            self._add_system_msg(GRANITE_SYSTEM_MESSAGE)
        self._add_system_msg(
            "You are an IBM prompt building assistant that helps the user build an instruction for a text summarization task. "
            "You will be interacting with two actors: system and user. The direct interaction will be only with system. "
            "The system will guide you through the stages necessary to build the prompt. "
            "Please answer only the word 'understood' if you understand these instructions. "
        )
        resp = self._get_assistant_response(max_new_tokens=10)
        self._add_assistant_msg(resp, 'hidden')
        assert resp.lower().startswith('understood')
        logging.info("initializing chat")

        intro_message = "Thanks. "\
            "Now, introduce yourself to the user, and present the following flow (do not act on this flow, just present it to the user): "\
            "1. You'll agree on an initial prompt based on some unlabeled data."\
            "2. You'll then refine the prompt based on the user's feedback on model outputs."\
            "3. You'll share the final few-shot prompt."\
            "\nMention to the user that after a prompt has been built, the user can evaluate it by clicking on Evaluate on the side-bar. "\
            "\nThen, suggest the user to select a dataset from our catalog, or to upload a csv file, where the first column contains the text inputs. "\
            "\nIf the user doesn't provide any evaluation data they can mention that in their response, and you'll proceed without it."
        self._add_system_msg(
            intro_message
        )
        static_assistant_hello_msg = ["Hello! I'm an IBM prompt building assistant, and I'm here to help you build an effective instruction, personalized to your text summarization task. At a high-level, we will work together through the following two stages - \n",
                                      "1.	Agree on an initial zero-shot prompt based on some unlabeled data you will share, and your feedback.\n",
                                      "2.	Refine the prompt and add a few examples, approved by you, to turn it into a few-shot prompt. \n",
                                      "At any stage you can evaluate the performance of the obtained prompt by clicking on \"Evaluate\" on the sidebar. Once done, you can download the prompt and use it for your task.\n",
                                      "To get started, please select a dataset from our catalogue or upload a CSV file containing the text inputs in the first column, with ‘text’ as the header. If you don't have any unlabeled data to share, please let me know, and we'll proceed without it.\n"]

        self._add_assistant_msg("\n".join(static_assistant_hello_msg), 'both')

    def _add_prompt(self, prompt, is_new=True):
        prompt = prompt.strip("\n")

        if is_new:
            self.approved_prompts.append({'prompt': prompt, 'stage': self.state})
        else:
            self.approved_prompts[-1]['prompt'] = prompt

    def _got_introduction_responses(self):
        self._add_system_msg(
            'Did User already respond to all of the questions you should be asking? Answer yes or no ONLY.')
        resp = self._get_assistant_response()
        self.hidden_chat = self.hidden_chat[:-1]  # remove the last question
        if resp.lower().startswith('yes'):
            return True
        return False

    def _continue_ask_introduction_questions(self):
        self._add_system_msg(
            'Ok, please ask the user the next question.')
        resp = self._get_assistant_response()
        self.hidden_chat = self.hidden_chat[:-1]
        self._add_assistant_msg(resp, 'both')

    def _need_clarification_from_the_user(self):
        self._add_system_msg(
            "Do you need any clarification on the user\'s respond? "
            "or maybe you are missing some details in understanding the user preferences? "
            "or maybe the user is not happy with the prompt you suggested but they do not say why? "
            "Answer yes or no ONLY."
        )
        resp = self._get_assistant_response()
        self.hidden_chat = self.hidden_chat[:-1]  # remove the last question
        if resp.lower().startswith('yes'):
            return True
        return False

    def _ask_clarification_question(self):
        self._add_system_msg(
            'Ok, please ask a clarification question if needed.')
        resp = self._get_assistant_response()
        self.hidden_chat = self.hidden_chat[:-1]
        self._add_assistant_msg(resp, 'both')

    def _extract_text_example(self):
        self._add_system_msg(
            'Did you obtain a text example from the user in the last message? answer "yes" or "no"')
        resp = self._get_assistant_response()
        self.hidden_chat = self.hidden_chat[:-1]  # remove the last question
        if resp.lower().startswith('yes'):
            example = self.user_chat[-1]['content']
            if "".join(example.split()) not in ["".join(ex.split()) for ex in self.text_examples]:
                self.text_examples.append(example)
                logging.info(f"Extracted text examples ({len(self.text_examples)}): {self.text_examples}")
            return True
        return False

    def _confirm_characteristics(self):
        self._add_system_msg(
            "What is your current understanding of the input texts and the expected properties of the summaries?")
        resp = self._get_assistant_response(max_new_tokens=200)
        # keep only the first paragraph, the model can go on
        if '\n' in resp:
            resp = resp[: resp.index('\n')]
        self._add_assistant_msg(resp, 'hidden')
        self._add_system_msg('Please validate your suggestion with the user, and update it if necessary.')
        resp = self._get_assistant_response(max_new_tokens=200)
        self._add_assistant_msg(resp, 'both')
        self.state = ConversationState.CONFIRM_CHARACTERISTICS

    def _confirm_prompt(self, is_new):
        if 'llama' in self.bam_client.parameters['model_id']:
            self._add_system_msg(
                'Build the summarization prompt based on your current understanding (only the instruction). '
                'Do not add any additional information besides the prompt.'
            )
            resp = self._get_assistant_response(max_new_tokens=200)
            prompt = resp
        elif 'mixtral' in self.bam_client.parameters['model_id']:
            self._add_system_msg(
                'Build the summarization prompt based on your current understanding (only the instruction). '
                'Enclose the prompt in triple quotes (```).'
            )
            resp = self._get_assistant_response(max_new_tokens=200)
            prompt = extract_delimited_text(resp, ['```', '"""'])
            prompt = prompt.strip("\"")
        else:  # granite
            self._add_system_msg('Build the prompt for summarization based on your current understanding '
                                 '(only the instruction). Enclose the prompt in triple quotes (```).')
            resp = self._get_assistant_response(max_new_tokens=200)
            prompt = extract_delimited_text(resp, ['```', '"""'])
            prompt = prompt.strip("\"")

        self._add_prompt(prompt, is_new=is_new)
        logging.info(f"added prompt: {prompt} | prompt is {'new' if is_new else 'corrected'}")
        self._add_assistant_msg(prompt, 'hidden')
        if 'granite' not in self.bam_client.parameters['model_id']:
            self._add_system_msg('Please validate your suggested prompt with the user, and update it if necessary.')
        else:  # granite
            self._add_system_msg('Share this prompt with the user, do not modify it, and ask them if it\'s ok or they would like to update it.')
        resp = self._get_assistant_response(max_new_tokens=200)
        self._add_assistant_msg(resp, 'both')

    def _user_asked_for_correction(self):
        if 'granite' not in self.bam_client.parameters['model_id']:
            self._add_system_msg(
                'Has the user asked for a correction or a modification of the suggested prompt in the last message? answer "yes" or "no"')
        else:  # granite
            self._add_system_msg('Please write "changed" if user suggested a change to the prompt, and "no changes" otherwise.')
        resp = self._get_assistant_response(max_new_tokens=50)
        self.hidden_chat = self.hidden_chat[:-1]  # remove the last question
        if 'granite' not in self.bam_client.parameters['model_id']:
            if resp.lower().startswith('yes'):
                return True
            return False
        else:  # granite
            if resp.lower().startswith('changed'):
                return True
            return False

    def _prompt_suggestion_accepted(self):
        self._add_system_msg(
            'Has the user accepted the prompt you suggested? Answer "yes" or "no"')
        resp = self._get_assistant_response(max_new_tokens=50)
        self.hidden_chat = self.hidden_chat[:-1]  # remove the last question
        if resp.lower().startswith('yes'):
            return True
        return False

    def _summary_suggestion_corrected(self):
        self._add_system_msg(
            'Has the user asked for a correction or a modification of the suggested summary? Answer "yes" or "no"')
        resp = self._get_assistant_response(max_new_tokens=50)
        self.hidden_chat = self.hidden_chat[:-1]  # remove the last question
        if resp.lower().startswith('yes'):
            return True
        return False

    def _prompts_should_be_corrected(self):
        self._add_system_msg(
            'Has the user asked for a correction or a modification of the prompt? Answer "yes" or "no"')
        resp = self._get_assistant_response(max_new_tokens=50)
        self.hidden_chat = self.hidden_chat[:-1]  # remove the last question
        if resp.lower().startswith('yes'):
            return True
        return False

    def _ask_for_text(self):
        self._add_system_msg(
            "Ask the user to provide up to three typical examples of the texts he or she wish to summarize. "
            "This will help you get familiar with the domain and the flavor of the user's documents. "
            "Mention to the user that they need to share three examples one at a time, "
            "but at each stage they can indicate that they do not have anymore examples to share. "
            "Please ask them to share only the clean text of the examples without any prefixes or suffixes. "
            "Do not share your insights until you have collected all examples."
        )
        resp = self._get_assistant_response(max_new_tokens=200)
        self._add_assistant_msg(resp, 'both')
        return resp

    def _do_nothing(self):
        resp = self._get_assistant_response(max_new_tokens=200)
        self._add_assistant_msg(resp, 'both')
        return resp

    def _ask_for_next_text(self):
        self._add_system_msg(
            "Ask the user for the next text example. "
            "Please remind them to share only the clean text of the examples without any prefixes or suffixes. "
        )
        resp = self._get_assistant_response(max_new_tokens=200)
        self._add_assistant_msg(resp, 'both')
        return resp

    def _has_more_texts(self):
        if self.user_has_more_texts:
            self._add_system_msg(
                'Has the user indicated they finished sharing texts (e.g. that they have no more examples to share), or not? Answer either "finished" or "not finished"')
            resp = self._get_assistant_response(max_new_tokens=20)
            self.hidden_chat = self.hidden_chat[:-1]  # remove the last question
            self.user_has_more_texts = ("not finished" in resp.lower()) or not (resp.lower().startswith("finished") or
                                                                                "no more" in resp.lower() or "have finished" in resp.lower() or "they finished sharing" in resp.lower())
            logging.info(f"user_has_more_texts is set to {self.user_has_more_texts}")
        return self.user_has_more_texts

    def _ask_text_questions(self):
        self._add_system_msg(
            "Now, if the user shared some examples, ask the user up to 5 relevant questions about his summary preferences. "
            "Please do not ask questions that refer to a specific example. "
            "Please clarify to the user that he doesn׳t need to answer all the questions, only those that he feels are relevant for his summary preferences. "
            "Ask the user to provide  all the answers at the same message. "
            "If the user did not provide any examples, ask only general questions about the prompt "
            "without mentioning that the user shared examples."
        )
        resp = self._get_assistant_response()
        # self.hidden_chat = self.hidden_chat[:-1]  # remove the last question
        self._add_assistant_msg(resp, 'both')

    def _evaluate_prompt(self):
        summary_correction = len(self.approved_summaries) > self.validated_example_idx
        prompt = self.approved_prompts[-1]['prompt']

        prompt_str = build_few_shot_prompt(prompt,
                                           self.approved_summaries[:self.validated_example_idx],
                                           self.bam_client.parameters['model_id'])
        example = self.text_examples[self.validated_example_idx]
        prompt_str = prompt_str.format(text=example)
        self._add_system_msg(prompt_str)
        summary = self._get_assistant_response()
        #summary = self.bam_client.send_messages(prompt_str)[0]

        if summary_correction:
            logging.info(f"correcting approved summary for example {self.validated_example_idx}")
            logging.info(f"new summary is {summary}")
            self.approved_summaries[self.validated_example_idx]['summary'] = summary
        else:
            self.approved_summaries.append({'text': example, 'summary': summary})

        #resp = self._get_assistant_response()
        resp = f"{summary}\n\nThis summary result is based on the text of example no. {(self.validated_example_idx + 1)} you shared. Is this summary satisfactory? If you'd like to make any changes or adjustments, please let me know!"
        self.hidden_chat = self.hidden_chat[:-2]  # remove the last messages

        self._add_assistant_msg(resp, 'both')

    def _share_prompt_and_save(self):
        prompt = self.approved_prompts[-1]['prompt']
        temp_chat = []
        if 'granite' not in self.bam_client.parameters['model_id']:
            self._add_msg(temp_chat, ChatRole.USER,
                          'Suggest a name for the following summarization prompt. '
                          'The name should be short and descriptive, it will be used as a title in the prompt library. '
                          f'Enclose the suggested name in triple quotes (```). The prompt is "{prompt}"')
            resp = self._get_assistant_response(temp_chat)
            name = extract_delimited_text(resp, "```").strip().replace('"', '').replace(" ", "_")[:50]
        else:  # granite
            name = self.dataset_name + "_" + str(self.conv_id)

        prompt_str = build_few_shot_prompt(prompt, self.approved_summaries[:self.validated_example_idx],
                                           self.bam_client.parameters['model_id'])
        final_msg = "Here is the final prompt: \n\n" + prompt_str
        saved_name, bam_url = self.bam_client.save_prompt(name, prompt_str)
        final_msg += f'\n\nThis prompt has been saved to your prompt Library under the name "{saved_name}". ' \
                     f'You can try it in the [BAM Prompt Lab]({bam_url}) or in the Evaluate tab. This prompt works best for model {self.bam_client.parameters["model_id"]}.'
        self._add_assistant_msg(final_msg, 'user')
        self.state = ConversationState.DONE

    def save_data(self):
        chat_dir = os.path.join(self.out_dir, "chat")
        os.makedirs(chat_dir, exist_ok=True)
        with open(os.path.join(chat_dir, "final_prompts.json"), "w") as f:
            approved_prompts = self.approved_prompts
            if self.state == ConversationState.CONFIRM_PROMPT:
                approved_prompts = approved_prompts[:-1] #the last prompt is not confirmed yet
            for p in self.approved_prompts:
                p['prompt_with_format'] = build_few_shot_prompt(p['prompt'], [], self.bam_client.parameters['model_id'])
                p['prompt_with_format_and_few_shots'] = build_few_shot_prompt(p['prompt'], self.approved_summaries[:self.validated_example_idx],
                                                                              self.bam_client.parameters['model_id'])
            json.dump(self.approved_prompts, f)
        with open(os.path.join(chat_dir, "config.json"), "w") as f:
            json.dump({"model": self.bam_client.parameters['model_id'], "dataset": self.dataset_name}, f)
        df = pd.DataFrame(self.user_chat)
        df.to_csv(os.path.join(chat_dir, "user_chat.csv"), index=False)
        df = pd.DataFrame(self.hidden_chat)
        df.to_csv(os.path.join(chat_dir, "hidden_chat.csv"), index=False)
        with open(os.path.join(chat_dir, "hidden_chat.html"),"w") as html_out:
            content = "\n".join([f"<p><b>{x['role'].upper()}: </b>{x['content']}</p>".replace("\n", "<br>") for x in self.user_chat])
            header = "<h1>IBM Research Conversational Prompt Engineering</h1>"
            html_template = f'<!DOCTYPE html><html>\n<head>\n<title>CPE</title>\n</head>\n<body style="font-size:20px;">{header}\n{content}\n</body>\n</html>'
            html_out.write(html_template)
            logging.info(f"conversation saved in {chat_dir}")

    def _no_texts(self):
        return len(self.text_examples) == 0

    def generate_agent_message(self):
        if (len(self.user_chat) > 0 and self.user_chat[-1]['role'] == ChatRole.ASSISTANT) or \
                (self.state == ConversationState.INITIALIZING):
            return None

        logging.info(f"in {self.state}")
        if self.state is None:
            self.state = ConversationState.INITIALIZING
            self._init_chats()
            self.state = ConversationState.INTRODUCTION

        elif self.state == ConversationState.INTRODUCTION:
            if len(self.text_examples) == 0:
                self.enable_upload_file = False
                logging.info(f"asking for text to summarize")
                self._ask_for_text()
                next_state = ConversationState.PROCESS_TEXTS
            else:
                raise ValueError("I should be inside this case!")
                instruction_txt = 'Look at the following text examples, and suggest a summarization prompt for them. ' \
                                  'Do not include the examples into the prompt. ' \
                                  'Enclose the suggested prompt in triple quotes (```).\n'
                self._add_system_msg(instruction_txt + '\n'.join(self.text_examples))
                resp = self._get_assistant_response(max_new_tokens=200)
                initial_prompt = extract_delimited_text(resp, '```')
                next_state = ConversationState.CONFIRM_PROMPT

            self.state = next_state

        elif self.state == ConversationState.CONFIRM_PROMPT:
            if self._user_asked_for_correction():
                logging.info("user asked for correction")
                if self._need_clarification_from_the_user():
                    logging.info("clarification question")
                    self._ask_clarification_question()
                else:
                    self._confirm_prompt(is_new=False)
            else:
                if self._prompt_suggestion_accepted():
                    logging.info("user accepted the suggested prompt")
                    if self.user_has_more_texts:
                        logging.info(f"asking for text to summarize")
                        self._ask_for_text()
                        self.state = ConversationState.PROCESS_TEXTS
                    else:
                        logging.info(
                            f"user gave {len(self.text_examples)} text examples. ({self.validated_example_idx})")
                        self._evaluate_prompt()
                        self.state = ConversationState.CONFIRM_SUMMARY
                else:
                    logging.info("user did not accept the prompt")
                    self._confirm_prompt(is_new=False)

        elif self.state == ConversationState.PROCESS_TEXTS:
            example_extracted = self._extract_text_example()
            if example_extracted:
                logging.info("extracted text from user")
            if self._has_more_texts() and len(self.text_examples) < NUM_USER_EXAMPLES:
                logging.info("ask the user for another example")
                self._ask_for_next_text()
            else:
                self.user_has_more_texts = False
                logging.info("ask questions on the examples provided")
                self._ask_text_questions()
                self.state = ConversationState.PROCESS_RESPONSES

        elif self.state == ConversationState.PROCESS_RESPONSES:
            self._confirm_prompt(is_new=True)
            self.state = ConversationState.CONFIRM_PROMPT

        elif self.state == ConversationState.EVALUATE_PROMPT:
            logging.info("extracted text from user")
            self._evaluate_prompt()
            self.state = ConversationState.CONFIRM_SUMMARY

        elif self.state == ConversationState.CONFIRM_SUMMARY:
            if not self._summary_suggestion_corrected():
                logging.info(f"user approved the summary of one example. ({self.validated_example_idx})")
                self.validated_example_idx += 1
                if self.validated_example_idx == len(self.text_examples):
                    logging.info("user approved all the summaries so ending the chat and sharing the final prompt")
                    self._share_prompt_and_save()
                    self.print_timing_report()
                else:
                    self._evaluate_prompt()
            else:
                logging.info("user did not approve the summary")
                if self._prompts_should_be_corrected():
                    logging.info("making changes to the prompt")
                    self._confirm_prompt(is_new=True)
                    self.state = ConversationState.CONFIRM_PROMPT
                else: # only the summary should be corrected
                    self._evaluate_prompt()


        elif self.state == ConversationState.DONE:
            self._add_msg(self.user_chat, ChatRole.ASSISTANT, 'Please press the "Reset" button to restart the session')

        self.save_data()
        return self.user_chat[-1]

    def process_examples(self, df, dataset_name):
        self.dataset_name = dataset_name
        self.enable_upload_file = False
        self.user_has_more_texts = False

        text_col = df.columns[0]  # can ask the model which column is most likely the text
        texts = df[text_col].tolist()
        self.text_examples = texts[:3]
        if len(texts) > 10:
            texts = texts[3:]
            random.shuffle(texts)

        max_len_tokens = self.bam_client.parameters[
                             'max_total_tokens'] - 1000  # random value to account for the role headers
        token_counts = self.bam_client.count_tokens(texts)
        total_len = 0
        num_examples = 0
        selected_examples = []
        for txt, token_count in zip(texts, token_counts):
            if total_len + token_count > max_len_tokens:
                break
            num_examples += 1
            total_len += token_count
            selected_examples.append(txt)

        temp_chat = []

        system_message = 'We are working on a tailored prompt for text summarization. ' \
                         'Following are few examples of texts to be summarized. ' \
                         'Describe common characteristics of those examples which may be relevant for the summarization.'

        if 'granite' not in self.bam_client.parameters['model_id']:
            self._add_msg(temp_chat, ChatRole.SYSTEM, system_message)
            for txt in selected_examples:
                self._add_msg(temp_chat, ChatRole.SYSTEM, txt)
        else:  # granite
            self._add_msg(temp_chat, ChatRole.SYSTEM, GRANITE_SYSTEM_MESSAGE)
            user_message = system_message + '\n'.join(selected_examples)
            self._add_msg(temp_chat, ChatRole.USER, user_message)

        characteristics = self._get_assistant_response(temp_chat)
        self._add_system_msg(f'The user has provided {num_examples} examples')
        self._add_msg(self.hidden_chat, ChatRole.ASSISTANT, characteristics)
        self._ask_text_questions()

        self.state = ConversationState.PROCESS_RESPONSES
        return self.user_chat[-1]

    def get_prompts(self):
        return self.approved_prompts
