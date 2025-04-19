document.addEventListener('DOMContentLoaded', function () {
    function initSlider(sliderContainer) {
        const prevBtn = sliderContainer.querySelector('.slider-btn-previous');
        const nextBtn = sliderContainer.querySelector('.slider-btn-next');
        const indicatorsContainer = sliderContainer.querySelector('.slider-indicators');

        let currentSlide = 0;
        let slideElements = [];
        let indicatorElements = [];
        let intervalId = null;
        const slideDuration = 5000; // 5 seconds

        // Create slide elements
        function createSlides() {
            sliderContainer.querySelectorAll('.slider-slide').forEach((slide, index) => {
                slideElements.push(slide);
            });
        }

        // Create indicators
        function createIndicators() {
            indicatorsContainer.innerHTML = ''; // Clear existing indicators
            slideElements.forEach((_, index) => {
                const indicator = document.createElement('button');
                indicator.className = 'h-3 w-3 rounded-full transition-all duration-300 bg-primary-100/50';
                indicator.dataset.index = index;
                indicator.setAttribute('aria-label', `Go to slide ${index + 1}`);

                indicator.addEventListener('click', () => {
                    goToSlide(index);
                });

                indicatorsContainer.appendChild(indicator);
                indicatorElements.push(indicator);
            });
        }

        // Show slide
        function showSlide(index) {
            // Hide all slides
            slideElements.forEach(slide => {
                slide.classList.remove('opacity-100');
                slide.classList.add('opacity-0');
            });

            // Show current slide
            slideElements[index].classList.remove('opacity-0');
            slideElements[index].classList.add('opacity-100');

            // Update indicators
            indicatorElements.forEach((indicator, i) => {
                if (i === index) {
                    indicator.classList.add('bg-secondary-600', 'w-6');
                    indicator.classList.remove('bg-primary-100/50');
                } else {
                    indicator.classList.remove('bg-secondary-600', 'w-6');
                    indicator.classList.add('bg-primary-100/50');
                }
            });

            currentSlide = index;
        }

        // Next slide
        function nextSlide() {
            const nextIndex = (currentSlide + 1) % slideElements.length;
            showSlide(nextIndex);
        }

        // Previous slide
        function prevSlide() {
            const prevIndex = (currentSlide - 1 + slideElements.length) % slideElements.length;
            showSlide(prevIndex);
        }

        // Go to specific slide
        function goToSlide(index) {
            showSlide(index);
            resetInterval();
        }

        // Start auto-advance
        function startInterval() {
            intervalId = setInterval(nextSlide, slideDuration);
        }

        // Reset auto-advance timer
        function resetInterval() {
            clearInterval(intervalId);
            startInterval();
        }

        // Initialize this specific slider
        createSlides();
        createIndicators();
        showSlide(0);
        startInterval();

        // Event listeners for this slider
        prevBtn.addEventListener('click', () => {
            prevSlide();
            resetInterval();
        });

        nextBtn.addEventListener('click', () => {
            nextSlide();
            resetInterval();
        });

        // Pause on hover for this slider
        sliderContainer.addEventListener('mouseenter', () => {
            clearInterval(intervalId);
        });

        sliderContainer.addEventListener('mouseleave', () => {
            resetInterval();
        });
    }

    document.querySelectorAll('.slider-container').forEach(initSlider);
});