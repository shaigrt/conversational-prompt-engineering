from concurrent.futures import ThreadPoolExecutor

from genai.schema import ChatRole

from conversational_prompt_engineering.backend.chat_manager_util import ChatManagerBase


class ModelPrompts:
    def __init__(self) -> None:
        self.task_instruction = \
            'You and I (system) will work together to build a prompt for summarization task for the user.' \
            'You will interact with the user to gather information, and discuss the summaries. ' \
            'I will generate the summaries from the prompts you suggest, and pass them back to you, ' \
            'so that you could discuss them with the user. ' \
            'User time is valuable, keep the conversation pragmatic. Make the obvious decisions by yourself.' \
            'Don\'t greet the user at your first interaction.'

        self.api_instruction = \
            'You should communicate with the user and system ONLY via python API described below, and not via direct messages. ' \
            'The input parameters to API functions are strings. Enclose them in double quotes, and escape all double quotes inside these strings. ' \
            'Format ALL your answers python code calling one of the following functions:'

        self.api = {
            'self.submit_message_to_user(message)': 'call this function to submit your message to the user. Use markdown to mark the prompts and the summaries.',
            'self.submit_prompt(prompt)': 'call this function to inform the system that you have a new suggestion for the prompt',
            'self.summary_accepted(example_num, summary)': 'call this function every time the user accepts a summary. Pass the example number and the summary text as parameters.',
            'self.done()': 'call this function when the user is satisfied with the prompt and the results it produces.',
        }

        self.examples_instruction = \
            'The user has provided the following examples for the texts to summarize, ' \
            'briefly discuss them with the user before suggesting the prompt. ' \
            'Your suggestion should take into account the user comments and corrections.' \
            'Share the suggested prompt with the user before submitting it.' \
            'Remember to communicate only via API calls.'

        self.result_intro = 'The suggested prompt has produced the following summaries for the user examples:'

        self.analyze_result_instruction = \
            'For each example show the full produced summary to the user and discuss it with them, one example at a time. ' \
            'The discussion should result in a summary accepted by the user.\n' \
            'When the user accepts a summary (directly or indirectly), call summary_accepted API passing the example number and the summary text. ' \
            'Continue your conversation with the user in any case.\n' \
            'Remember that the goal is a prompt that would directly produce summaries like approved by the user.\n' \
            'If this goal is achieved - inform the user and call done(). If the summaries had to be adjusted, suggest a new prompt that would produce those summaries directly.\n' \
            'Remember to communicate only via API calls.'

        self.syntax_err_instruction = 'The last API call produced a syntax error. Return the same call with fixed error.'


class CallbackChatManager(ChatManagerBase):
    def __init__(self, bam_api_key, model, conv_id) -> None:
        super().__init__(bam_api_key, model, conv_id)
        self.model_prompts = ModelPrompts()

        self.api_names = None

        self.model_chat = []
        self.model_chat_length = 0
        self.user_chat = []
        self.user_chat_length = 0

        self.dataset_name = None
        self.enable_upload_file = True

        self.examples = None
        self.summaries = None
        self.prompts = []

    @property
    def approved_prompts(self):
        return [{'prompt': p} for p in self.prompts]

    @property
    def approved_summaries(self):
        return [{'text': t, 'summary': s} for t, s in zip(self.examples, self.summaries) if s is not None]

    @property
    def validated_example_idx(self):
        return len([s for s in self.summaries if s is not None])

    def add_system_message(self, msg):
        self._add_msg(self.model_chat, ChatRole.SYSTEM, msg)

    def submit_model_chat_and_process_response(self):
        if len(self.model_chat) > self.model_chat_length:
            resp = self._get_assistant_response(self.model_chat)
            self._add_msg(self.model_chat, ChatRole.ASSISTANT, resp)
            self.model_chat_length = len(self.model_chat)
            api_indices = sorted([resp.index(name) for name in self.api_names if name in resp])
            api_calls = [resp[begin: end].strip() for begin, end in zip(api_indices, api_indices[1:] + [len(resp)])]
            for call in api_calls:
                escaped_call = call.replace('\n', '\\n')
                try:
                    exec(escaped_call)
                except SyntaxError:
                    self.add_system_message(self.model_prompts.syntax_err_instruction)
                    self.submit_model_chat_and_process_response()

    def add_user_message(self, message):
        self._add_msg(self.user_chat, ChatRole.USER, message)
        self.user_chat_length = len(self.user_chat)  # user message is rendered by cpe
        self._add_msg(self.model_chat, ChatRole.USER, message)

    def add_welcome_message(self):
        static_assistant_hello_msg = ["Hello! I'm an IBM prompt building assistant, and I'm here to help you build an effective instruction, personalized to your text summarization task. At a high-level, we will work together through the following two stages - \n",
                                      "1.	Agree on an initial zero-shot prompt based on some unlabeled data you will share, and your feedback.\n",
                                      "2.	Refine the prompt and add a few examples, approved by you, to turn it into a few-shot prompt. \n",
                                      "At any stage you can evaluate the performance of the obtained prompt by clicking on \"Evaluate\" on the sidebar. Once done, you can download the prompt and use it for your task.\n",
                                      "To get started, please select a dataset from our catalogue or upload a CSV file containing the text inputs in the first column, with ‘text’ as the header. If you don't have any unlabeled data to share, please let me know, and we'll proceed without it.\n"]

        self._add_msg(chat = self.user_chat, role = ChatRole.ASSISTANT, msg= "\n".join(static_assistant_hello_msg))


    def generate_agent_messages(self):
        self.submit_model_chat_and_process_response()
        agent_messages = []
        if len(self.user_chat) > self.user_chat_length:
            for msg in self.user_chat[self.user_chat_length:]:
                if msg['role'] == ChatRole.ASSISTANT:
                    agent_messages.append(msg)
            self.user_chat_length = len(self.user_chat)
        self.save_chat_html(self.user_chat, "user_chat.html")
        self.save_chat_html(self.model_chat, "model_chat.html")
        return agent_messages

    def submit_message_to_user(self, message):
        self._add_msg(self.user_chat, ChatRole.ASSISTANT, message)

    def submit_prompt(self, prompt):
        self.prompts.append(prompt)

        futures = {}
        with ThreadPoolExecutor(max_workers=len(self.examples)) as executor:
            for i, example in enumerate(self.examples):
                tmp_chat = []
                self._add_msg(tmp_chat, ChatRole.SYSTEM, prompt + '\Text: ' + example + '\nSummary: ')
                futures[i] = executor.submit(self._get_assistant_response, tmp_chat)

        self.add_system_message(self.model_prompts.result_intro)
        for i, f in futures.items():
            summary = f.result()
            self.add_system_message(f'Example {i + 1}: {summary}')

        self.add_system_message(self.model_prompts.analyze_result_instruction)

        self.submit_model_chat_and_process_response()

    def summary_accepted(self, example_num, summary):
        example_idx = int(example_num) - 1
        self.summaries[example_idx] = summary

    def done(self):
        # placeholder
        pass

    def set_instructions(self, task_instruction, api_instruction, function2description):
        self.api_names = [key[:key.index('(')] for key in function2description.keys()]
        self.add_system_message(task_instruction)
        self.add_system_message(api_instruction)
        for fun_sign, fun_descr in function2description.items():
            self.add_system_message(f'function {fun_sign}: {fun_descr}')

    def init_chat(self, examples):
        self.set_instructions(self.model_prompts.task_instruction, self.model_prompts.api_instruction,
                              self.model_prompts.api)

        self.summaries = [None] * len(examples)
        self.examples = examples
        self.add_system_message(self.model_prompts.examples_instruction)
        for i, ex in enumerate(self.examples):
            self.add_system_message(f'Example {i + 1}: {ex}')

        self.submit_model_chat_and_process_response()

    def process_examples(self, df, dataset_name):
        self.dataset_name = dataset_name
        self.enable_upload_file = False
        examples = df['text'].tolist()[:3]
        self.init_chat(examples)
