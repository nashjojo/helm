import csv
import os
import random
from collections import defaultdict
from typing import Dict, List, Optional

from common.general import shell, ensure_file_downloaded, ensure_directory_exists
from common.hierarchical_logger import hlog
from .scenario import Scenario, InformationRetrievalInstance, Reference, TRAIN_SPLIT, VALID_SPLIT, CORRECT_TAG


class MSMARCOScenario(Scenario):
    """MS MARCO (Microsoft Machine Reading Comprehension) is a collection of
    datasets, based on the following research paper:

        https://arxiv.org/abs/1611.09268

    All the datasets can be retrieved at:

        https://microsoft.github.io/msmarco/

    The original dataset has 1,010,916 anonymized queries and "8,841,823
    passages extracted from 3,563,535 web documents retrieved by Bing". There
    are several tasks within the MS MARCO family, and each uses a variation
    of the aforementioned passage and query datasets.

    In our implementation, we are focusing on the Passage Retrieval task,
    which is an information retrieval task where the goal is to find the best
    passage that contains an answer to a given query. The evaluation set, which
    has 6980 queries, released with the task does not have the reference
    matches, so we use a subset of the development set as our evaluation set.

    We frame the passage retrieval task as a binary classification problem,
    similar to https://arxiv.org/pdf/2003.06713.pdf. Specifically, given a
    passage and a query, the model's job is to predict whether the passage
    includes an answer to the query by selecting one of the "yes" or "no"
    options. Shared below is an example of how a query with 4 context examples
    may look like.

        Passage: To access Data Import: 1  Sign in to Google Analytics. 2  Select the
        Admin tab and navigate to the property to which you want to upload the
        data. 3  Click Data Import. 4  This displays the Data Sets page.
        Question: Does the passage above answer the question effects of hydrogen
        combustion?
        A. Yes
        B. No
        Answer: B

        Passage: Sarcoidosis (sar-koy-DO-sis) is a disease of unknown cause that leads to
        inflammation. This disease affects your bodyâs organs. Normally, your
        immune system defends your body against foreign or harmful substances. For
        example, it sends special cells to protect organs that are in danger.
        Question: Does the passage above answer the question what causes sarcoidosis
        of the lungs?
        A. Yes
        B. No
        Answer: A

        Passage: Carbonic acid is a weak acid that is produced when carbon dioxide is dissolved
        in water. As you probably know, our atmosphere has a lot of carbon dioxide in
        it.It is also thoroughly saturated with water.From this, we might deduce that
        we live in a rather acidic environment â and we do.arbonic acid is a weak
        acid that is produced when carbon dioxide is dissolved in water. As you probably
        know, our atmosphere has a lot of carbon dioxide in it. It is also thoroughly
        saturated with water. From this, we might deduce that we live in a rather acidic
        environment â and we do.
        Question: Does the passage above answer the question what is a affidavit of support?
        A. Yes
        B. No
        Answer: B

        Passage: One of the FHAâs primary criteria is whether or not youâve owned a home.
        If youâve never owned a home, youâre considered a first-time homebuyer.
        But you are allowed to be a previous homeowner and still qualify as a first-time
        homebuyer. According to the FHA, you can do so if you have not been an owner in a
        primary residence for at least three years leading up to your purchase.
        Question: Does the passage above answer the question what is considered first
        time home buyer?
        A. Yes
        B. No
        Answer: A

        Passage: http://en.wikipedia.org/wiki/William_Bradford_(Plymouth_Colony_governor) William
        Bradford (c.1590 â 1657) was an English Separatist leader in Leiden, Holland
        and in Plymouth Colony was a signatory to the Mayflower Compact. He served as
        Plymouth Colony Governor five times covering about thirty years between 1621 and 1657.
        Question: Does the passage above answer the question how many years did william
        bradford serve as governor of plymouth colony?
        A. Yes
        B. No
        Answer:

    For each query, we assign a ranking to each passage that we queried the model with
    as follows:
        - We get the model's answer, "Yes" or "No", and the logprob of the answer
            for each passage.
        - We rank the answers we got using the following scheme:
            High => "Yes", high logprob
                 => "Yes", low  logprob
                 => "No",  low  logprob
            Low  => "No",  high logprob

    Once we have a ranked list of passages for a query, we compute MRR@10,
    which is the mean reciprocal rank of the gold passage when we only
    consider the top 10 passages.

    Below are some details on the datasets we use, which can all be retrieved
    at the link below, pointing to a 1GB tar file. Here, "qid" stands for
    "Query ID" and "pid" stands for "Passage ID". FORMAT column specifies the
    contents of each file, where \t is used as the delimiter character.

        https://msmarco.blob.core.windows.net/msmarcoranking/collectionandqueries.tar.gz

                  FILE          |            INFO           |      FORMAT
        `collection.tsv`        | 8,841,823 passages        | <pid> <passage text>
        `qrels.dev.small.tsv`   | 7437      query relations | <qid> 0 <pid> 1
        `qrels.train.tsv`       | 532,761   query relations | <qid> 0 <pid> 1
        `queries.dev.small.tsv` | 6980      queries         | <qid> <query text>
        `queries.train.tsv`     | 808,731   queries         | <qid> <query text>

    `qrels` files contain the query relations, mapping each
    query (with the ID qid) to a ground truth passage match (with the ID pid).
    Note that there are more matches than the number of queries: this
    happens because the `qrels` file sometimes contain 2 best passage matches
    for a query.

    We also utilize two custom generated files, `top1000_bm25_dev.tsv` (133 MB)
    and `top20_bm25_train.tsv`. These files contain the top 1000 and 20 best
    passage id matches for a given query in the dev or train set, respectively.
    We have generated these files using the BM25 algorithm. Both of these files
    as well as the notebook including our file generation code can be found at
    the following Codalab link:

        https://worksheets.codalab.org/worksheets/0xf451c0dec2a6414aae0b68e8e325426c

    The topk files have the following format, where rank is a number between
    1 and 1000:

        <qid> <pid> <rank>

    For details on how we create the instances, refer to the docs of the
    `get_instances` method.

    For details on how we evaluate our results, please refer to the
    `MSMARCOMetric` class in `msmarco_metric.py`.
    """

    """ Information on this class """
    name = "msmarco"
    description = "Microsoft Machine Reading Comprehension"
    tags = ["information_retrieval"]

    # CLASS VARIABLES
    """ Names of the tasks and tracks that we support """
    PASSAGE_TASK = "passage"
    REGULAR_TRACK = "regular"
    TREC_TRACK = "trec"
    TASK_NAMES: List[str] = [PASSAGE_TASK]
    TRACK_NAMES: List[str] = [REGULAR_TRACK, TREC_TRACK]

    """ The filename of the top1000 file created with the BM25 algorithm """
    TOPK_DEV_FILE_NAME: str = "top1000_bm25.dev.tsv"
    TOPK_TRAIN_FILE_NAME: str = "top20_bm25.train.tsv"

    """ The base URL for the MSMARCO datasets """
    MSMARCO_URL: str = "https://msmarco.blob.core.windows.net/msmarcoranking"

    """ Codalab URL format """
    CODALAB_URL: str = "https://worksheets.codalab.org/rest/bundles/{bundle}/contents/blob/"

    """" Codalab dev url """
    CODALAB_DEV_BUNDLE: str = "0x004852a9a16d4a99851b6151a1972d36"
    CODALAB_DEV_URL: str = CODALAB_URL.format(bundle=CODALAB_DEV_BUNDLE)

    """ Codalab train url """
    CODALAB_TRAIN_BUNDLE: str = "0x499c07699f3f4881a787b6a5249f4466"
    CODALAB_TRAIN_URL: str = CODALAB_URL.format(bundle=CODALAB_TRAIN_BUNDLE)

    """ The maximum number of queries that we can run the scenario for.

    Eval queries capped at 6980 since that is the size of the dev set we use.
    Note that each eval query results in multiple instances. Train queries
    capped at 808731 as that's the size of the train set.
    """
    MAX_NUM_EVAL_QUERIES = {
        REGULAR_TRACK: 6980,
        TREC_TRACK: 200,
    }
    MAX_NUM_TRAIN_QUERIES = 808731

    """ The max number of extra best gold evaluation instances we want to include.

    The information retrieval metric used with this scenario provides bounds for the best case
    by ensuring that all the gold instances are included in the rankings, in addition to scoring
    the topk without this intervention.
    """
    MAX_NUM_EXTRA_GOLD_INSTANCES = {
        REGULAR_TRACK: 2,
        TREC_TRACK: 5,  # TODO look at the distribution
    }

    """ The relation values that we consider to be gold for a given track.

    This is the value that is read from the qrels file for a given track. Depending on
    the track, we interpret these values differently: For example, for the regular track,
    qrel value of 1 means that the match is a gold match. For TREC, however, value of
    1 means a match that's not great, and values of 2 and 3 denotes good matches.
    """
    GOLD_RELATIONS = {
        REGULAR_TRACK: [1],
        TREC_TRACK: [2, 3],  # TODO look at the values
    }

    """ Upper and lower bounds on topk, the number of top passages for a given query.

    Capped at 1000 because our pre-generated topk file (TOP1000_DEV_FILE_NAME) only
    contains the top 1000 passage ids per dev query.

    Topk should at least be 11 as our default metric is MRR@10. We have 1 gold
    instance for each query where we have the matching passage. We must have 9
    non-matching instances to ensure that there are 10 total instances. There can be
    up to 2 gold queries in the top 11 passage list for a given query. This means
    that we can get at least 9 non-matching instances from the top 11 list.
    """
    MAX_TOPK: int = 1000
    MIN_TOPK: int = 11

    """ The minumum rank we will accept for the no instances.

    This is to ensure that when creating the no instances, we do not consider the
    First several ranks in our train topk list, which may contain passages similar
    to the gold passages.
    """
    TRAIN_MAX_NO_INSTANCE_RANK: int = 20
    TRAIN_MIN_NO_INSTANCE_RANK: int = 11

    """ Yes and no answer strings """
    YES_ANSWER = "Yes"
    NO_ANSWER = "No"

    def __init__(
        self, task: str, track: str, num_eval_queries: int = 100, topk: int = 30, num_train_queries: int = 1000
    ):
        """MSMARCOScenario class constructor.

        Both outlined below, `topk` and `num_eval_queries` have a direct impact
        on the number of tokens used by this scenario, given a specific `task`.

        For the Passage Retrieval task, you can find the total number of tokens
        needed as follows:

            num_no_examples_per_query = topk, or topk - 1, or topk - 2
            total_num_eval_instances = num_eval_queries * (1 + num_no_examples_per_query)
            total_num_tokens = total_num_eval_instances * (1 + NUM_CONTEXT_EXAMPLES) * AVG_TOKEN_LENGTH

        In the above formulation:
            - NUM_CONTEXT_EXAMPLES corresponds to the number of training
                examples we add to the context of each request.
            - AVG_TOKEN_LENGTH is the average token length of one instance, which
                is about 535 tokens on average.

        Args:
            task: Name of the task, should be one of self.TASK_NAMES. There are
                several MSMARCO tasks, and we use the task parameter to specify
                which task we would like performed. There is only one task that
                is implemented for the time being: the Passage Retrieval task,
                which can be called by passing "passage" for the task value.
            track: Name of the track. There is only one track implemented for
                now, which is the "regular" track.
            num_eval_queries: Number of evaluation queries that are used to
                create eval instances. Must be smaller than or equal to
                self.MAX_NUM_EVAL_QUERIES for the given track. The total
                number of evaluation instances created is a function of this number:

                    num_no_examples_per_query = topk - m, where m is the number of top gold instances
                                                in topk.
                    total_num_eval_instances = num_eval_queries * (1 + num_no_examples_per_query)
            topk: To find the best passage match for a given validation query,
                instead of going through all the passages in the collection, we
                only look at a select number of filtered passages, which is
                determined by `topk`. Must be in the range
                (self.MIN_TOPK, self.MAX_TOPK].
            num_train_queries: Number of train queries that are used to crete
                the train instances. Must be smaller than or equal to
                self.MAX_NUM_TRAIN_QUERIES. The total number of training instances
                created is a function of this number:

                    num_no_examples_per_query = 1
                    total_num_train_instances = num_train_queries * (1 + num_no_examples_per_query)
        """
        # Random generator for our scenario
        self.random: random.Random = random.Random(1885)

        # Task
        self.task: str = task
        assert self.task in self.TASK_NAMES

        # Track
        self.track: str = track
        assert self.track in self.TRACK_NAMES

        # The max number of extra best gold evaluation instances we want to include given the track.
        self.max_num_extra_gold_instances = self.MAX_NUM_EXTRA_GOLD_INSTANCES[self.track]

        # The relation values we consider to be gold, changes based on the track.
        self.gold_relations = self.GOLD_RELATIONS[self.track]

        # num_eval_queries
        if num_eval_queries > self.MAX_NUM_EVAL_QUERIES[self.track]:
            msg = f"""
                Number of evaluation queries for the {self.track} track should not be bigger than
                {self.MAX_NUM_EVAL_QUERIES[self.track]}.
            """
            raise ValueError(msg)

        # TopK
        if topk < self.MIN_TOPK or topk > self.MAX_TOPK:
            msg = f"Number of passages ranked should be between {self.MIN_TOPK} and {self.MAX_TOPK} (both inclusive)."
            raise ValueError(msg)
        self.topk = topk

        # num_train_queries
        if num_train_queries > self.MAX_NUM_TRAIN_QUERIES:
            msg = f"Number of train queries should not be bigger than {self.MAX_NUM_TRAIN_QUERIES}."
            raise ValueError(msg)

        # Set num queries
        self.num_queries = {VALID_SPLIT: num_eval_queries, TRAIN_SPLIT: num_train_queries}

        # List of ranks we will consider for the no instances.
        #   By default, we consider all the ranks up to and including self.topk
        # For the train set, we start the no instance ranks at self.TRAIN_MIN_NO_INSTANCE_RANK
        #   to ensure we don't include passages that are good potentials for the gold matches.
        self.no_ranks = {
            VALID_SPLIT: list(range(1, self.topk + 1)),
            TRAIN_SPLIT: list(
                range(self.TRAIN_MIN_NO_INSTANCE_RANK, min(self.topk + 1, self.TRAIN_MAX_NO_INSTANCE_RANK))
            ),
        }

        # Initialize the data dictionaries that will be populated once the MSMARCO scenario is run
        self.collection_dict: Dict[int, str] = {}
        self.queries_dicts: Dict[str, Dict[int, str]] = {}
        self.qrels_dicts: Dict[str, Dict[int, Dict[int, int]]] = {}
        self.topk_dicts: Dict[str, Dict[int, Dict[int, int]]] = {}

    def download_file(
        self, source_url: str, file_name: str, unpack: bool = False, unpack_type: Optional[str] = None
    ) -> str:
        """ Downloads a file.

        Writes the file in the given source_url a file with the name file_name
        in the /data directory in located in the self.output_path.
        """
        data_path = os.path.join(self.output_path, "data")
        ensure_directory_exists(data_path)
        file_path: str = os.path.join(data_path, file_name)
        ensure_file_downloaded(source_url=source_url, target_path=file_path, unpack=unpack, unpack_type=unpack_type)
        return file_path

    @staticmethod
    def create_id_item_dictionary(file_path: str) -> Dict[int, str]:
        """ Reads .tsv files in the following format into Python dictionaries:

            <id>    <text>

        For example, if the file contents look like:

            1   this is the first example
            2   this is the second example
            3   this is the third example

        The dictionary returned would be as follows:
            {
                1: "this is the first example",
                2: "this is the second example",
                3: "this is the third example"
            }

        Returns:
            id_to_item_dict: Dictionary mapping the id of an item to the item.
        """
        id_to_item_dict = {}
        with open(file_path, encoding="utf-8") as f:
            for _id, content in csv.reader(f, delimiter="\t"):
                id_to_item_dict[int(_id)] = content
        return id_to_item_dict

    @staticmethod
    def create_qrels_dictionary(file_path: str, delimiter="\t") -> Dict[int, Dict[int, int]]:
        """ Reads .tsv files in the following format into a Python dictionary:

            <qid>   0   <pid>   1
            <qid>   0   <pid>   2
            <qid>   0   <pid>   0

        The last number in each row indicates the relationship of the query with
        the ID qid and passage with the ID pid. O indicates that the passage
        is not relevant for the query. A number >= 1 indicates that the passage
        is relevant, where higher numbers indicates higher relevance.

        The qrels files shared with common MSMARCO tasks are not exhaustive.
        For example, all the relevance values in the main MSMARCO passage
        retrieval qrel file are 1: The file only contains the matching qids
        with no indication of how good each match is. The qrel file for the
        TREC passage retrieval task would have relevance values that are 0,
        but only for a few pids for a given qid. It is also possible for a qid
        to have multiple pid matches with the same relevance score.

        For example, if the file contents look like:

            11111111   0    12837901     1
            11111111   0    82374921     2
            11111111   0    23028102     1
            22222222   0    28192830     1
            ...


        The dictionary returned would be as follows:
            {
                11111111: {
                    12837901: 1,
                    82374921: 2,
                    23028102: 1,
                },
                22222222: {
                    28192830: 1
                },
            }

        Returns:
            qrels_dict: Dictionary mapping a qid to a dictionary mapping a
                pid to relevance.
        """
        dictionary: Dict[int, Dict[int, int]] = defaultdict(dict)
        with open(file_path, encoding="utf-8") as f:
            for qid, _, pid, qrel in csv.reader(f, delimiter=delimiter):
                dictionary[int(qid)][int(pid)] = int(qrel)
        return dictionary

    @staticmethod
    def create_topk_dictionary(file_path: str) -> Dict[int, Dict[int, int]]:
        """ Reads .tsv files in the following format into a Python dictionary:

            <qid>\t<pid>\t<rank>

        For example, if the file contents look like:

            11111111   12837901     1
            11111111   82374921     2
            11111111   28192830     3
            ...
            11111111   28191237     1000
            22222222   98021301     1
            22222222   21938912     2
            22222222   12938010     3
            ...
            22222222   32409810     1000

        The dictionary returned would be as follows:
            {
                11111111: {
                    1: 12837901,
                    2: 82374921,
                    3: 28192830,
                    ...
                    1000: 28191237
                },
                22222222: {
                    1: 98021301,
                    2: 21938912,
                    3: 12938010,
                    ...
                    1000: 32409810
                }
            }

        Returns:
            topk_dictionary: Dictionary mapping a qid to a dictionary mapping
                ranks to a pid.
        """
        topk_dict: Dict[int, Dict[int, int]] = defaultdict(dict)
        with open(file_path, encoding="utf-8") as f:
            for qid, pid, rank in csv.reader(f, delimiter="\t"):
                topk_dict[int(qid)][int(rank)] = int(pid)
        return topk_dict

    def prepare_passage_dictionaries(self, track: str):
        """ Downloads the Passage Retrieval datasets and reads them into dictionaries.

        Args:
            track: The track we are going to be using, which affects the query and qrels
                   files we use.

        Sets the following:
            self.collection_dict: Mapping pid to passage.
            self.queries_dicts: Dictionary containing query dictionaries mapping a
                qid to a query.

                {
                    VALID_SPLIT: valid_query_dict,
                    TRAIN_SPLIT: train_query_dict
                }
            self.qrels_dicts: Dictionary containing qrels dictionaries mapping a
                qid to a list of gold pids. Refer to
                self.create_qrels_dictionary for the exact format of the sub
                dictionaries.

                {
                    VALID_SPLIT: valid_qrels_dict,
                    TRAIN_SPLIT: train_qrels_dict
                }
        """

        # Get datasets
        hlog("Downloading MSMARCO collection and queries.")
        cq_path = self.download_file(
            f"{self.MSMARCO_URL}/collectionandqueries.tar.gz", "collectionandqueries", unpack=True, unpack_type="untar"
        )

        # Collection
        self.collection_dict = self.create_id_item_dictionary(os.path.join(cq_path, "collection.tsv"))

        # Queries and Qrels
        if self.track == self.REGULAR_TRACK:
            valid_queries_dict = self.create_id_item_dictionary(os.path.join(cq_path, "queries.dev.small.tsv"))
            valid_qrels_dict = self.create_qrels_dictionary(os.path.join(cq_path, "qrels.dev.small.tsv"))
        elif self.track == self.TREC_TRACK:
            # Get queries
            tsv_file_name = "msmarco-test2019-queries.tsv"
            q_path = self.download_file(f"{self.MSMARCO_URL}/{tsv_file_name}.gz", f"{tsv_file_name}.gz")
            shell(["gzip", "-d", q_path])
            valid_queries_dict = self.create_id_item_dictionary(os.path.join(self.output_path, "data", tsv_file_name))
            # Get qrels
            qrel_path = self.download_file("https://trec.nist.gov/data/deep/2019qrels-pass.txt", "2019qrels-pass.txt")
            valid_qrels_dict = self.create_qrels_dictionary(qrel_path, delimiter=" ")
        else:
            msg = f"Track name {self.track} is not a valid track in {self.TRACK_NAMES}."
            raise ValueError(msg)

        self.queries_dicts = {
            TRAIN_SPLIT: self.create_id_item_dictionary(os.path.join(cq_path, "queries.train.tsv")),
            VALID_SPLIT: valid_queries_dict,
        }

        # Query relations
        self.qrels_dicts = {
            TRAIN_SPLIT: self.create_qrels_dictionary(os.path.join(cq_path, "qrels.train.tsv")),
            VALID_SPLIT: valid_qrels_dict,
        }

    def prepare_topk_dictionaries(self):
        """ Downloads the topk files and reads them into dictionaries.

        Args:
            track: The track we are going to be using, which affects the query and qrels
                   files we use.

        Sets the following field:
            self.topk_dicts: Dictionary containing topk dictionaries mapping a
                qid to a dictionary mapping a rank to a pid. Refer to
                self.create_topk_dict for the exact format of the sub
                dictionaries.

                {
                    VALID_SPLIT: valid_topk_dict,
                    TRAIN_SPLIT: train_topk_dict
                }
        """
        hlog("Downloading topk files.")

        # Get files
        topk_dev_fp = self.download_file(self.CODALAB_DEV_URL, self.TOPK_DEV_FILE_NAME)
        topk_train_fp = self.download_file(self.CODALAB_TRAIN_URL, self.TOPK_TRAIN_FILE_NAME)
        self.topk_dicts = {
            TRAIN_SPLIT: self.create_topk_dictionary(topk_train_fp),
            VALID_SPLIT: self.create_topk_dictionary(topk_dev_fp),
        }

    @staticmethod
    def make_context(passage: str, query: str) -> str:
        """ Makes the context text given a passage and a query. """
        # Remove a question mark at the end of the query, if there is any
        if query[-1] == "?":
            query = query[:-1]
        question_statement = f"Does the passage above answer the question {query}?"
        return f"{passage}\nQuestion: {question_statement}"

    def get_instance(
        self, qid: int, pid: int, split: str, qrel: Optional[int] = None, rank: Optional[int] = None
    ) -> InformationRetrievalInstance:
        """ Creates an instance.

        Args:
            qid: Query id.
            pid: Passage id.
            split: TRAIN_SPLIT or VALID_SPLIT.
            qrel: Relevance of the passage for the query. None if the relevance
                is unknown. 0 indicates no relation. The higher the number,
                higher the relevance.
            rank: Rank of passage for the query.

        Returns:
            instance: Created instances.
        """
        query = self.queries_dicts[split][qid]
        passage = self.collection_dict[pid]
        context = self.make_context(passage, query)
        is_relevant = qrel in self.gold_relations
        references = [
            Reference(output=self.YES_ANSWER, tags=[CORRECT_TAG] if is_relevant else []),
            Reference(output=self.NO_ANSWER, tags=[] if is_relevant else [CORRECT_TAG]),
        ]
        instance = InformationRetrievalInstance(
            input=context, references=references, split=split, qid=qid, oid=pid, qrel=qrel, rank=rank
        )
        return instance

    def get_passage_split_instances(self, split) -> List[InformationRetrievalInstance]:
        """ Creates instances for the specified split.

        For the number of queries specified for each split, we loop through the
        query list. For each query:
            - We create a "yes" instance, where the included passage is the gold
                passage for the given query.
            - We then create a set of "no" instances by going through the topk
                passage list for the query. We select all the passages that are
                not in the gold passages list for the query.

                We limit the number of no examples for the train split to be 1 to
                ensure that we have a balanced train split.

                We do not consider the first several ranks for the train queries to
                ensure that the no examples we include in the train split are not the
                highly ranked false positives.

        Args:
            split: VALID_SPLIT or TRAIN_SPLIT.

        Returns:
            instances: List of instances created.
        """
        # Helper function for getting the top gold pids.
        def get_gold_pids_sorted(rel_dict):
            gold_pairs = [(pid, rel) for pid, rel in rel_dict.items() if rel in self.gold_relations]
            self.random.shuffle(gold_pairs)
            gold_pids_sorted = [pair[0] for pair in sorted(gold_pairs, key=lambda p: p[1], reverse=True)]
            return gold_pids_sorted

        # Sample num_queries queries, specified in the constructor. We first shuffle then select our queries to
        #   ensure that we make use of the server side caching as the num_queries parameter is increased.
        qrels_keys = list(self.qrels_dicts[split].keys())
        self.random.shuffle(qrels_keys)
        qrels_keys = qrels_keys[: self.num_queries[split]]
        qrels_dict = {k: self.qrels_dicts[split][k] for k in qrels_keys}

        instances = []
        for qid, rel_dict in qrels_dict.items():

            # Get the data structures we will use.
            rank_to_pid = self.topk_dicts[split][qid]
            pid_to_rank = {p: r for r, p in rank_to_pid.items()}

            # Create pid lists.
            gold_pids_sorted = get_gold_pids_sorted(rel_dict)
            yes_pids_topk, no_pids_topk = [], []
            for r in self.no_ranks[split]:
                if r not in rank_to_pid:
                    hlog(f"{split}: For qid {qid}, pid with rank {r} is not known.")
                elif rank_to_pid[r] in gold_pids_sorted:
                    yes_pids_topk.append(rank_to_pid[r])
                else:
                    no_pids_topk.append(rank_to_pid[r])

            # Create instances for splits.
            split_pids = set()
            # For TRAIN split, we pick the best gold pid as well as 1 random no pid.
            if split == TRAIN_SPLIT:
                if gold_pids_sorted:
                    split_pids.add(gold_pids_sorted[0])
                if no_pids_topk:
                    split_pids.add(self.random.choice(no_pids_topk))
            elif split == VALID_SPLIT:
                # For VALID split, we use all the pids after capping the number of gold pids included so that
                #   we only have the best top gold pids.
                if len(gold_pids_sorted) > self.max_num_extra_gold_instances:
                    gold_pids_sorted = gold_pids_sorted[: self.max_num_extra_gold_instances]
                split_pids.update(gold_pids_sorted + yes_pids_topk + no_pids_topk)

            # Once we have the pid values, we can create the instances
            pids = list(split_pids)
            self.random.shuffle(pids)
            for pid in split_pids:
                rank = pid_to_rank[pid] if pid in pid_to_rank else None
                rel = rel_dict[pid] if pid in rel_dict else None
                instances.append(self.get_instance(qid, pid, split, rel, rank))

        return instances

    def get_passage_instances(self, track: str = "regular") -> List[InformationRetrievalInstance]:
        """ Gets instances for the passage task. """
        # Get dataset and topk dictionaries
        self.prepare_passage_dictionaries(track)
        self.prepare_topk_dictionaries()

        # Create instances
        valid_instances = self.get_passage_split_instances(VALID_SPLIT)
        train_instances = self.get_passage_split_instances(TRAIN_SPLIT)
        instances = valid_instances + train_instances

        return instances

    def get_instances(self) -> List[InformationRetrievalInstance]:
        """ Gets instances for the MSMARCO class.

        Supported tasks and the corresponding method called to get instances:
            "passage": self.get_passage_instances()

        Refer to the documentation of the methods above for details on how the
        instances are created.
        """
        if self.task == "passage":
            return self.get_passage_instances(self.track)
        raise ValueError(f"Task must be one of {', '.join(self.TASK_NAMES)}")
