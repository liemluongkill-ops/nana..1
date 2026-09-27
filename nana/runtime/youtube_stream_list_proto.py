"""Minimal runtime protobuf schema for YouTube ``liveChatMessages.streamList``.

The official YouTube sample normally generates these classes with
``grpcio-tools``.  Nana already has grpcio/protobuf at runtime, but not the
compiler package, so this module builds the small wire-compatible subset it
needs dynamically.  Unknown protobuf fields are intentionally ignored.
"""

from __future__ import annotations

from google.protobuf import descriptor_pb2, descriptor_pool
from google.protobuf.internal import builder as _builder
from google.protobuf import symbol_database as _symbol_database


_sym_db = _symbol_database.Default()


def _add_field(
    message: descriptor_pb2.DescriptorProto,
    name: str,
    number: int,
    field_type: int,
    *,
    label: int = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL,
    type_name: str | None = None,
) -> None:
    field = message.field.add()
    field.name = name
    field.number = number
    field.label = label
    field.type = field_type
    if type_name:
        field.type_name = type_name


def _build_file_descriptor() -> bytes:
    file_proto = descriptor_pb2.FileDescriptorProto()
    file_proto.name = "youtube_stream_list.proto"
    file_proto.package = "youtube.api.v3"
    file_proto.syntax = "proto2"

    service = file_proto.service.add()
    service.name = "V3DataLiveChatMessageService"
    method = service.method.add()
    method.name = "StreamList"
    method.input_type = ".youtube.api.v3.LiveChatMessageListRequest"
    method.output_type = ".youtube.api.v3.LiveChatMessageListResponse"
    method.server_streaming = True

    request = file_proto.message_type.add()
    request.name = "LiveChatMessageListRequest"
    _add_field(request, "live_chat_id", 1, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _add_field(request, "hl", 2, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _add_field(request, "profile_image_size", 3, descriptor_pb2.FieldDescriptorProto.TYPE_UINT32)
    _add_field(request, "max_results", 98, descriptor_pb2.FieldDescriptorProto.TYPE_UINT32)
    _add_field(request, "page_token", 99, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _add_field(
        request,
        "part",
        100,
        descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
        label=descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED,
    )

    response = file_proto.message_type.add()
    response.name = "LiveChatMessageListResponse"
    _add_field(response, "kind", 200, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _add_field(response, "etag", 201, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _add_field(response, "offline_at", 2, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _add_field(
        response,
        "page_info",
        1004,
        descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE,
        type_name=".youtube.api.v3.PageInfo",
    )
    _add_field(response, "next_page_token", 100602, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _add_field(
        response,
        "items",
        1007,
        descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE,
        label=descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED,
        type_name=".youtube.api.v3.LiveChatMessage",
    )
    _add_field(
        response,
        "active_poll_item",
        1008,
        descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE,
        type_name=".youtube.api.v3.LiveChatMessage",
    )

    message = file_proto.message_type.add()
    message.name = "LiveChatMessage"
    _add_field(message, "kind", 200, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _add_field(message, "etag", 201, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _add_field(message, "id", 101, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _add_field(
        message,
        "snippet",
        2,
        descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE,
        type_name=".youtube.api.v3.LiveChatMessageSnippet",
    )
    _add_field(
        message,
        "author_details",
        3,
        descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE,
        type_name=".youtube.api.v3.LiveChatMessageAuthorDetails",
    )

    author = file_proto.message_type.add()
    author.name = "LiveChatMessageAuthorDetails"
    _add_field(author, "channel_id", 10101, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _add_field(author, "channel_url", 102, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _add_field(author, "display_name", 103, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _add_field(author, "profile_image_url", 104, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _add_field(author, "is_verified", 4, descriptor_pb2.FieldDescriptorProto.TYPE_BOOL)
    _add_field(author, "is_chat_owner", 5, descriptor_pb2.FieldDescriptorProto.TYPE_BOOL)
    _add_field(author, "is_chat_sponsor", 6, descriptor_pb2.FieldDescriptorProto.TYPE_BOOL)
    _add_field(author, "is_chat_moderator", 7, descriptor_pb2.FieldDescriptorProto.TYPE_BOOL)

    snippet = file_proto.message_type.add()
    snippet.name = "LiveChatMessageSnippet"
    event_enum = snippet.enum_type.add()
    event_enum.name = "Type"
    for name, number in (
        ("INVALID_TYPE", 0),
        ("TEXT_MESSAGE_EVENT", 1),
        ("TOMBSTONE", 2),
        ("FAN_FUNDING_EVENT", 3),
        ("CHAT_ENDED_EVENT", 4),
        ("SPONSOR_ONLY_MODE_STARTED_EVENT", 5),
        ("SPONSOR_ONLY_MODE_ENDED_EVENT", 6),
        ("NEW_SPONSOR_EVENT", 7),
        ("USER_BANNED_EVENT", 10),
        ("SUPER_CHAT_EVENT", 15),
        ("SUPER_STICKER_EVENT", 16),
        ("MEMBER_MILESTONE_CHAT_EVENT", 17),
        ("MEMBERSHIP_GIFTING_EVENT", 18),
        ("GIFT_MEMBERSHIP_RECEIVED_EVENT", 19),
        ("POLL_EVENT", 20),
        ("GIFT_EVENT", 21),
    ):
        enum_value = event_enum.value.add()
        enum_value.name = name
        enum_value.number = number
    _add_field(
        snippet,
        "type",
        1,
        descriptor_pb2.FieldDescriptorProto.TYPE_ENUM,
        type_name=".youtube.api.v3.LiveChatMessageSnippet.Type",
    )
    _add_field(snippet, "live_chat_id", 201, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _add_field(snippet, "author_channel_id", 301, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _add_field(snippet, "published_at", 4, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _add_field(snippet, "has_display_content", 17, descriptor_pb2.FieldDescriptorProto.TYPE_BOOL)
    _add_field(snippet, "display_message", 16, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _add_field(
        snippet,
        "text_message_details",
        19,
        descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE,
        type_name=".youtube.api.v3.LiveChatTextMessageDetails",
    )
    _add_field(
        snippet,
        "super_chat_details",
        27,
        descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE,
        type_name=".youtube.api.v3.LiveChatSuperChatDetails",
    )

    text_details = file_proto.message_type.add()
    text_details.name = "LiveChatTextMessageDetails"
    _add_field(text_details, "message_text", 1, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)

    super_chat = file_proto.message_type.add()
    super_chat.name = "LiveChatSuperChatDetails"
    _add_field(super_chat, "amount_micros", 1, descriptor_pb2.FieldDescriptorProto.TYPE_UINT64)
    _add_field(super_chat, "currency", 2, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _add_field(super_chat, "amount_display_string", 3, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _add_field(super_chat, "user_comment", 4, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
    _add_field(super_chat, "tier", 5, descriptor_pb2.FieldDescriptorProto.TYPE_UINT32)

    page_info = file_proto.message_type.add()
    page_info.name = "PageInfo"
    _add_field(page_info, "total_results", 1, descriptor_pb2.FieldDescriptorProto.TYPE_INT32)
    _add_field(page_info, "results_per_page", 2, descriptor_pb2.FieldDescriptorProto.TYPE_INT32)

    return file_proto.SerializeToString()


DESCRIPTOR = descriptor_pool.Default().AddSerializedFile(_build_file_descriptor())
_builder.BuildMessageAndEnumDescriptors(DESCRIPTOR, globals())
_builder.BuildTopDescriptorsAndMessages(
    DESCRIPTOR,
    "nana.runtime.youtube_stream_list_proto",
    globals(),
)


__all__ = [
    "DESCRIPTOR",
    "LiveChatMessage",
    "LiveChatMessageAuthorDetails",
    "LiveChatMessageListRequest",
    "LiveChatMessageListResponse",
    "LiveChatMessageSnippet",
    "LiveChatTextMessageDetails",
]
