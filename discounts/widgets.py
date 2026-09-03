from django import forms


class AsyncMultiSelectWidget(forms.Widget):
    """Render only selected values; candidate records are loaded by a staff-only endpoint."""

    template_name = "discounts/widgets/async_multi_select.html"
    allow_multiple_selected = True

    def __init__(self, attrs=None, *, search_url=""):
        super().__init__(attrs)
        self.search_url = search_url
        self.selected_options = ()

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context["widget"].update(
            {
                "search_url": self.search_url,
                "selected_options": self.selected_options,
            }
        )
        return context

    def value_from_datadict(self, data, files, name):
        if hasattr(data, "getlist"):
            return data.getlist(name)
        value = data.get(name, [])
        if value in (None, ""):
            return []
        return list(value) if isinstance(value, (list, tuple)) else [value]

    def value_omitted_from_data(self, data, files, name):
        return name not in data
