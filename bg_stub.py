import flet as ft

# Background stub utilities


def get_background_size(content_control: ft.Control, padding: int = 0):
    """
    Determine the size needed for the background based on content dimensions.
    Returns (width, height) tuple. If content has no explicit size, returns (None, None)
    to let the container self-size.
    """
    try:
        width = getattr(content_control, 'width', None)
        height = getattr(content_control, 'height', None)
        
        # If content has explicit dimensions, add padding
        if width is not None:
            width = width + (padding * 2)
        if height is not None:
            height = height + (padding * 2)
            
        return width, height
    except Exception:
        return None, None


def create_background_control(page: ft.Page = None, path: str = None):
    """
    Return a background control placeholder that will be dynamically sized.
    This is a bright green container for debugging.
    """
    try:
        return ft.Container(
            bgcolor="#00FF00",
            expand=False,
        )
    except Exception:
        return ft.Container(bgcolor="#00FF00", expand=False)


def apply_pattern_to_control(container: ft.Container, pattern: str = None):
    """
    Apply a pattern to an existing container. Stub — currently a noop.
    """
    try:
        # Future work: paint a pattern onto the container's background.
        # For now, do nothing to avoid visible changes.
        return container
    except Exception:
        return container


def create_background_stack(content_control: ft.Control, page: ft.Page = None, padding: int = 12, path: str = None):
    """
    Create a Stack with content on top and a dynamically-sized background behind it.
    The background size is determined by content size + padding.

    Implementation:
    - Creates a Stack with the content control on top
    - Creates a background container that will be sized based on content
    - Uses a resize listener to keep background size in sync with content
    """
    try:
        # Create the background container (green for debug)
        background = create_background_control(page, path)
        
        # Function to update background size when content size changes
        def update_background_size(e=None):
            try:
                # Get content dimensions
                content_width = getattr(content_control, 'width', None)
                content_height = getattr(content_control, 'height', None)
                
                # Calculate background size (add padding on all sides)
                if content_width is not None:
                    background.width = content_width + (padding * 2)
                if content_height is not None:
                    background.height = content_height + (padding * 2)
                
                # Position background behind content with negative margin (padding effect)
                background.offset_x = -padding
                background.offset_y = -padding
                
                if page:
                    page.update()
            except Exception:
                pass
        
        # Attach resize listeners to content and background
        if hasattr(content_control, 'on_resize'):
            content_control.on_resize = update_background_size
        
        # Create the Stack with background behind content
        return ft.Stack(
            expand=True,
            controls=[
                background,        # Behind (green for debug, dynamically sized)
                content_control,   # On top (content)
            ]
        )
    except Exception:
        # Fallback: just return content if something fails
        return content_control
