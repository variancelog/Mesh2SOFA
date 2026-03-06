# Calculation of DFHRTF in generate_extras.py

When calculating a Diffuse Field HRTF, we must average the frequency responses from all measured directions. However, measurement grids (like those in a SOFA file) are rarely distributed perfectly evenly. To prevent densely packed measurement points from overpowering sparsely packed ones, generate_extras.py mathematically compensates for the uneven geometry by assigning a spatial weight to each point.

Here is how the script calculates these weights:

### Step 1: Projecting onto a Sphere

* The first part of the script (`spherical_to_cartesian`) takes the measurement points from the evaluation grid and projects them onto a sphere (it doesn't assume the evaluation grid was spherical). The specific function driving this is `calculate_geometric_weights`, which extracts the azimuth and elevation from the SOFA file and explicitly passes a radius of `1.0` into the `spherical_to_cartesian` function, forcing every point onto a perfect unit sphere regardless of its original recorded distance.

### Step 2: Assigning the Weights

The function responsible for this entire section is `calculate_geometric_weights`. First, it uses `scipy.spatial.ConvexHull` to figure out which points connect to form the triangles (called `simplices` in the code).
* **Formula:** It calculates the area using the **Cross Product** of two edges of the triangle. If a triangle has corners A, B, and C, the formula for its area is half the magnitude of the cross product of vectors AB and AC:

$$Area = \frac{1}{2} \| (\vec{B} - \vec{A}) \times (\vec{C} - \vec{A}) \|$$

* **In the code:** `area = 0.5 * np.linalg.norm(np.cross(B - A, C - A))`

* It then simply divides each area by 3. Because `ConvexHull` groups the triangles by their three corner indices (`simplex`), the code loops through every triangle and adds a third of its area to the running total for each of those three specific corner points.

$$Weight_{point} = Weight_{point} + \frac{Area}{3}$$

* **In the code:** `weights[simplex] += area / 3.0` (Because `simplex` contains 3 points, NumPy automatically adds the value to all three).

* To find the final percentage weight, it takes each point's accumulated area and divides it by the sum of *all* accumulated areas (which is effectively the total surface area of the sphere).

$$Final Weight_i = \frac{Accumulated Area_i}{\sum All Accumulated Areas}$$


* **In the code:** `weights /= np.sum(weights)`