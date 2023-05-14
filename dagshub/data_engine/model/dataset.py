import json
import logging
import os.path
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING, Union

import httpx
from dataclasses_json import dataclass_json

import dagshub.auth
from dagshub.auth.token_auth import HTTPBearerAuth
from dagshub.common import config
from dagshub.data_engine.model.errors import WrongOperatorError, WrongOrderError, DatasetFieldComparisonError
from dagshub.data_engine.model.query import DatasetQuery, _metadataTypeLookup

if TYPE_CHECKING:
    from dagshub.data_engine.model.datasources import DataSource
    from dagshub.data_engine.client.data_client import QueryResult
    import fiftyone as fo

logger = logging.getLogger(__name__)


@dataclass_json
@dataclass
class DataPointMetadataUpdateEntry(json.JSONEncoder):
    url: str
    key: str
    value: str
    valueType: str


class Dataset:

    def __init__(self, datasource: "DataSource", query: Optional[DatasetQuery] = None):
        self._source = datasource
        if query is None:
            query = DatasetQuery(self)
        self._query = query

        self._include_list: List[str] = []
        self._exclude_list: List[str] = []

    @property
    def include_list(self):
        """List of urls of datapoints to always be included in query results """
        return self._include_list

    @include_list.setter
    def include_list(self, val):
        self._include_list = val

    @property
    def exclude_list(self):
        """List of urls of datapoints to always be excluded in query results """
        return self._exclude_list

    @exclude_list.setter
    def exclude_list(self, val):
        self._exclude_list = val

    @property
    def source(self):
        return self._source

    def __deepcopy__(self, memodict={}) -> "Dataset":
        res = Dataset(self._source, self._query.__deepcopy__())
        res.include_list = self.include_list.copy()
        res.exclude_list = self.exclude_list.copy()
        return res

    def get_query(self):
        return self._query

    def serialize_gql_query_input(self):
        return {
            "query": self._query.serialize_graphql(),
            "include": self.include_list if len(self.include_list) > 0 else None,
            "exclude": self.exclude_list if len(self.exclude_list) > 0 else None,
        }

    def head(self) -> "QueryResult":
        return self._source.client.head(self)

    def all(self) -> "QueryResult":
        return self._source.client.get_datapoints(self)

    @contextmanager
    def metadata_context(self) -> "MetadataContextManager":
        ctx = MetadataContextManager(self)
        yield ctx
        self.source.client._update_metadata(self, ctx.get_metadata_entries())

    def __str__(self):
        return f"<Dataset source:{self._source}, query: {self._query}>"

    def save_dataset(self):
        logger.info(f"Saving dataset")
        raise NotImplementedError

    def to_voxel51_dataset(self) -> "fo.Dataset":
        import fiftyone as fo
        logger.info("Migrating dataset to voxel51")
        name = self._source.name
        ds: fo.Dataset = fo.Dataset(name)
        # ds.persistent = True
        dataset_location = os.path.join(Path.home(), "dagshub_datasets")
        os.makedirs(dataset_location, exist_ok=True)
        logger.info("Downloading files...")
        # Load the dataset from the query

        # FIXME: shouldnt use peek here, but only peekresult has the dataframe
        datapoints = self.all()

        host = config.host
        client = httpx.Client(auth=HTTPBearerAuth(dagshub.auth.get_token(host=host)))

        samples = []

        # TODO: parallelize this with some async magic
        for datapoint in datapoints.entries:
            file_url = datapoint.downloadUrl
            resp = client.get(file_url)
            assert resp.status_code == 200
            # TODO: doesn't work with nesting
            filename = file_url.split("/")[-1]
            filepath = os.path.join(dataset_location, filename)
            with open(filepath, "wb") as f:
                f.write(resp.content)
            sample = fo.Sample(filepath=filepath)
            sample["url"] = file_url
            for k, v in datapoint.metadata.items():
                sample[k] = v
            samples.append(sample)
        logger.info(f"Downloaded {len(datapoints.dataframe['name'])} file(s) into {dataset_location}")
        ds.add_samples(samples)
        return ds

    """ FUNCTIONS RELATED TO QUERYING
    These are functions that overload operators on the DataSet, so you can do pandas-like filtering
        ds = Dataset(...)
        queried_ds = ds[ds["value"] == 5]
    """

    def __getitem__(self, column_or_query: Union[str, "Dataset"]):
        new_ds = self.__deepcopy__()
        if type(column_or_query) is str:
            new_ds._query = DatasetQuery(new_ds, column_or_query)
            return new_ds
        else:
            # "index" is a dataset with a query - compose with "and"
            # Example:
            #   ds = Dataset()
            #   filtered_ds = ds[ds["aaa"] > 5]
            #   filtered_ds2 = filtered_ds[filtered_ds["bbb"] < 4]
            if self._query.is_empty:
                new_ds._query = column_or_query._query
                return new_ds
            else:
                return column_or_query.__and__(self)

    def __gt__(self, other: Union[int, float, str]):
        self._test_not_comparing_other_ds(other)
        return self.add_query_op("gt", other)

    def __ge__(self, other: Union[int, float, str]):
        self._test_not_comparing_other_ds(other)
        return self.add_query_op("ge", other)

    def __le__(self, other: Union[int, float, str]):
        self._test_not_comparing_other_ds(other)
        return self.add_query_op("le", other)

    def __lt__(self, other: Union[int, float, str]):
        self._test_not_comparing_other_ds(other)
        return self.add_query_op("lt", other)

    def __eq__(self, other: Union[int, float, str]):
        self._test_not_comparing_other_ds(other)
        return self.add_query_op("eq", other)

    def __ne__(self, other: Union[int, float, str]):
        self._test_not_comparing_other_ds(other)
        return self.add_query_op("ne", other)

    def __contains__(self, item):
        raise WrongOperatorError("Use `ds.contains(a)` for querying instead of `a in ds`")

    def contains(self, item: str):
        if type(item) is not str:
            return WrongOperatorError(f"Cannot use contains with non-string value {item}")
        self._test_not_comparing_other_ds(item)
        return self.add_query_op("contains", item)

    def __and__(self, other: "Dataset"):
        return self.add_query_op("and", other)

    def __or__(self, other: "Dataset"):
        return self.add_query_op("or", other)

    # Prevent users from messing up their queries due to operator order
    # They always need to put the dataset query filters in parentheses, otherwise the binary and/or get executed before
    def __rand__(self, other):
        if type(other) is not Dataset:
            raise WrongOrderError(type(other))
        raise NotImplementedError

    def __ror__(self, other):
        if type(other) is not Dataset:
            raise WrongOrderError(type(other))
        raise NotImplementedError

    def add_query_op(self, op: str, other: [str, int, float, "Dataset"]) -> "Dataset":
        """
        Returns a new dataset with an added query param
        """
        new_ds = self.__deepcopy__()
        if type(other) is Dataset:
            other = other.get_query()
        new_ds._query.compose(op, other)
        return new_ds

    @staticmethod
    def _test_not_comparing_other_ds(other):
        if type(other) is Dataset:
            raise DatasetFieldComparisonError()


class MetadataContextManager:
    def __init__(self, dataset: Dataset):
        self._dataset = dataset
        self._metadata_entries: List[DataPointMetadataUpdateEntry] = []

    def update_metadata(self, datapoints: Union[List[str], str], metadata: Dict[str, Any]):
        if isinstance(datapoints, str):
            datapoints = [datapoints]
        for dp in datapoints:
            for k, v in metadata.items():
                self._metadata_entries.append(DataPointMetadataUpdateEntry(
                    url=dp,
                    key=k,
                    value=str(v),
                    # todo: preliminary type check
                    valueType=_metadataTypeLookup[type(v)]
                ))

    def get_metadata_entries(self):
        return self._metadata_entries
