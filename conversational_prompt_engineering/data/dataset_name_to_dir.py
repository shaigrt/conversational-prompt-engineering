dataset_name_to_dir = {"Movie Reviews" : {"train":
                                              "./data/movie reviews/train.csv",
                                          "eval": "./data/movie reviews/eval.csv",
                                          "desc": "This dataset consists of movie reviews \npublished at https://www.rogerebert.com/."},
                       "Hotels and Restaurants": {"train": "./data/multiwoz/train.csv",
                                         "eval": "./data/multiwoz/test.csv",
                                         "eval_llm": "./data/multiwoz/test_full.csv",
                                         "desc": "This dataset consists of multi-turn dialogues \nabout hotel or restaurant reservation. "},
                        "Privacy Policies and Software Licenses": {"train":
                                             "./data/legal_plain_english/train.csv",
                                         "eval": "./data/legal_plain_english/eval.csv",
                                         "eval_llm": "./data/legal_plain_english/test_full.csv",
                                        "desc": "This dataset consists of passages from legal documents \ndiscussing privacy policies or software licenses."},
                       "IBM blog": {"train": "./data/ibm blog/train.csv",
                                                "eval": "./data/ibm blog/test.csv",
                                                 "desc": "This dataset contains blog entries from IBM blog."},
                       "climate blog": {"train": "./data/climate blog/train.csv",
                                    "eval": "./data/climate blog/test.csv",
                                    "desc": "This dataset consists of articles about climate related issues \npublished in https://www.climaterealityproject.org."},

                       "Reddit posts": {"train": "./data/tldr/train.csv",
                                        "eval": "./data/tldr/test.csv",
                                        "eval_llm": "./data/tldr/test_full.csv",
                                        "desc": "This dataset consists of posts from Reddit (TL;DR dataset)"},

                       "Restaurant reviews": {"train": "./data/lentricote_trip_advisor/train.csv",
                                        "eval": "./data/lentricote_trip_advisor/test.csv",
                                        "desc": "This dataset consists of reviewes of the restaurant\n \"L'entrecote\" in London, posted on the trip-advisor website"}

                       }