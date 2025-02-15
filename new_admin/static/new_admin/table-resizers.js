document.addEventListener('DOMContentLoaded', function() {
    const tableHeaders = document.querySelector("table.resizable").querySelectorAll('th');
    tableHeaders.forEach((th, index) => {
        if (index < tableHeaders.length - 1) {
            const resizer = document.createElement('div');
            resizer.classList.add('resizer');
            th.appendChild(resizer);
        }
    });
    const columnResizers = document.querySelectorAll('.resizer');
    let currentResizer;
    let startX;
    let startWidth;
    let th;

    columnResizers.forEach(resizer => {
        resizer.parentElement.style.width = resizer.parentElement.offsetWidth + 'px';
        resizer.addEventListener('mousedown', function(e) {
            currentResizer = e.target;
            startX = e.pageX;
            th = currentResizer.parentElement;
            startWidth = th.offsetWidth;
            document.addEventListener('mousemove', resizeColumn);
            document.addEventListener('mouseup', stopResize);
        });
    });

    function resizeColumn(e) {
        console.log(e.pageX, startX, startWidth);

        th.style.width = `${startWidth + (e.pageX - startX)}px`;
    }

    function stopResize() {
        document.removeEventListener('mousemove', resizeColumn);
        document.removeEventListener('mouseup', stopResize);
    }
});