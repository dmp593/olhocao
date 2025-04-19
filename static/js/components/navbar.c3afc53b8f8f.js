document.addEventListener('DOMContentLoaded', () => {
    // Language Toggle
    document.getElementById('language-toggle').addEventListener('click', function () {
        const currentLang = document.documentElement.lang
        const newLang = currentLang === 'en' ? 'pt' : 'en'
        
        document.cookie = `django_language=${newLang}; path=/; max-age=31536000`
        window.location.reload()
    })

    for (const element of document.querySelectorAll('[data-target]')) {
        element.addEventListener('click', function (event) {
            const target = document.getElementById(element.dataset.target)

            target.classList.toggle('hidden')
            target.classList.toggle('scale-y-0')
            target.classList.toggle('scale-y-100')
        })
    }

    const elementsWhichDataTargetsCloseOnOutsideClick = document.querySelectorAll('[data-target-close-on-outside-click="true"]');

    document.addEventListener('click', function(event) {
        for (const element of elementsWhichDataTargetsCloseOnOutsideClick) {
            if (! element.contains(event.target)) {
                const target = document.getElementById(element.dataset.target)

                target.classList.add('hidden')
                target.classList.remove('scale-y-100')
                target.classList.add('scale-y-0')
            }
        }
    })
});