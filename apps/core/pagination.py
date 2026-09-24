from collections import OrderedDict

from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class StandardResultsPagination(PageNumberPagination):
    """?page=1&page_size=20, page_size capped at 100. Wraps in the standard envelope."""

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response(self, data):
        return Response(
            OrderedDict(
                [
                    ("success", True),
                    ("message", "Success"),
                    (
                        "data",
                        OrderedDict(
                            [
                                ("results", data),
                                ("count", self.page.paginator.count),
                                ("num_pages", self.page.paginator.num_pages),
                                ("current_page", self.page.number),
                                ("page_size", self.get_page_size(self.request)),
                                ("next", self.get_next_link()),
                                ("previous", self.get_previous_link()),
                            ]
                        ),
                    ),
                    ("errors", None),
                ]
            )
        )

    def get_paginated_response_schema(self, schema):
        return {
            "type": "object",
            "properties": {
                "success": {"type": "boolean", "example": True},
                "message": {"type": "string", "example": "Success"},
                "data": {
                    "type": "object",
                    "properties": {
                        "results": schema,
                        "count": {"type": "integer", "example": 123},
                        "num_pages": {"type": "integer", "example": 7},
                        "current_page": {"type": "integer", "example": 1},
                        "page_size": {"type": "integer", "example": 20},
                        "next": {"type": "string", "nullable": True, "format": "uri"},
                        "previous": {"type": "string", "nullable": True, "format": "uri"},
                    },
                },
                "errors": {"type": "object", "nullable": True},
            },
        }
