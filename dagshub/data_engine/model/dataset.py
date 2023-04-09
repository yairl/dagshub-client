import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from dagshub.data_engine.model.datapoints import DatapointCollection
from dagshub.data_engine.model.query import Query

if TYPE_CHECKING:
    from dagshub.data_engine.model.datasources import DataSource

logger = logging.getLogger(__name__)

class Dataset:

    def __init__(self, datasource: "DataSource", query: Optional[Query] = None):
        self._source = datasource
        if query is None:
            query = Query(self)
        self._ds_query = query
        self._include_list: Optional[DatapointCollection] = None
        self._exclude_list: Optional[DatapointCollection] = None

    @property
    def source(self):
        return self._source

    def include(self):
        """Force adds datapoints to the returned set. They will show up even if they don't pass the query"""
        raise NotImplementedError

    def exclude(self):
        """Excludes datapoints from the returned set. They will not show up even if they pass the query"""
        raise NotImplementedError

    def _query(self, query_operand="and", param_operand="and", **query_params):
        """
        Composites a new dataset out of this dataset's query and the new query

        query_operand decides the operand between the dataset's query and the new query
        filter_operand decides the operand used between the query parameters
        """

        new_query = Query.from_query_params(self, param_operand, **query_params)
        return Dataset(datasource=self._source, query=self._ds_query.compose(new_query, query_operand))

    def and_query(self, param_operand="and", **query_params):
        return self._query("and", param_operand, **query_params)

    def or_query(self, param_operand="and", **query_params):
        return self._query("or", param_operand, **query_params)

    def peek(self):
        return self._source.client.query(self)

    def __str__(self):
        return f"<Dataset source:{self._source}, query: {self._ds_query}>"

    def save_dataset(self):
        logger.info(f"Saving dataset")
