// Appends rights IDs to form URL query parameters

document.addEventListener('DOMContentLoaded', function() {

    const checkboxes = document.querySelectorAll('.checkbox--rights');

    function updateUrl() {
        const checked = Array.prototype.slice.call(checkboxes).filter(x => x.checked).map(x => x.id)
        let formUrl = new URL(document.getElementById('approve_form').action)
        let searchParams = new URLSearchParams(formUrl.search)
        searchParams.set('rights_ids', checked.join(','))
        formUrl.search = searchParams.toString()
        document.getElementById('approve_form').action = formUrl
    }

    checkboxes.forEach(function(el) {
            el.addEventListener('click', updateUrl)
        }
    )

});