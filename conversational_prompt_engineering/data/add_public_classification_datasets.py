import os

import pandas as pd
import numpy as np
from collections import Counter

source_data_dir = "/Users/oritht/Projects/language-model-utilization/data"


def count_words(texts):
    num_words = []
    for t in texts:
        num_words.append(len(t.split(" ")))
    return num_words


def select_data(split, df, num_samples_selected, class_name=None):
    print("\nData split", split)
    print("Before filter", len(df))

    # Select a specific class
    if class_name is not None:
        df = df[df['label'].str.contains(class_name)]
        print(f"After selecting class name {class_name}", len(df))
    num_words = count_words(df['text'].tolist())
    print("min num words:", min(num_words), "max num words:", max(num_words), "avg num words:", int(np.mean(num_words)), "std num_words:", int(np.std(num_words)))

    # Filter text length and randomaize
    lower_limit = min(500, int(np.mean(num_words)))
    upper_limit = 1000
    print("num words: lower limit:", lower_limit, "upper limit:", upper_limit)
    selected_samples = [True if (n > lower_limit) and (n < upper_limit) else False for n in num_words]
    df_out = df[selected_samples].sample(frac=1, random_state=0)
    print("After text length filter", len(df_out))

    # Select num samples
    if num_samples_selected is not None:
        df_out = df_out['text'][:num_samples_selected]
    print("Final num samples", len(df_out))
    return df_out


def process_dataset(dataset_name, selected_class, df_train, df_test):
    print("\n", dataset_name, Counter(df_train['label']))
    os.makedirs(f'public/{dataset_name}', exist_ok=True)

    df_out = select_data("Train", df_train, 10, selected_class)
    df_out.to_csv(f'public/{dataset_name}/train.csv', index=False)

    df_out = select_data("Test", df_test, 8, selected_class)
    df_out.to_csv(f'public/{dataset_name}/test.csv', index=False)

    df_out = select_data("Test Full", df_test, None, selected_class)
    df_out.to_csv(f'public/{dataset_name}/test_full.csv', index=False)


dataset_name = 'reuters'
class_name = 'acquisition'
df_train = pd.read_csv(f"{source_data_dir}/{dataset_name}21578/train.csv")
df_test = pd.read_csv(f"{source_data_dir}/{dataset_name}21578/test.csv")
process_dataset(dataset_name, class_name, df_train, df_test)

dataset_name = '20_newsgroup'
class_name = 'space'
df_train = pd.read_csv(f"{source_data_dir}/{dataset_name}/train.csv")
df_test = pd.read_csv(f"{source_data_dir}/{dataset_name}/test.csv")
process_dataset(dataset_name, class_name, df_train, df_test)

dataset_name = 'cfpb'
class_name = 'credit reporting or credit repair services or other personal consumer reports'
df_train = pd.read_csv(f"{source_data_dir}/CFPB_Product/train.csv")
df_test = pd.read_csv(f"{source_data_dir}/CFPB_Product/test.csv")
process_dataset(dataset_name, class_name, df_train, df_test)


