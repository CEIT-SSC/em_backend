export interface ChangePassword {
    oldPassword: string;
    newPassword: string;
    newPasswordConfirm: string;
}

export interface EmailVerification {
    /**
    * @format email
    */
    email: string;
    /**
    * @minLength 6
    * @maxLength 6
    */
    code: string;
}

export interface ErrorResponse {
    error: string;
}

export interface MessageResponse {
    message: string;
}

export interface ResendVerificationEmail {
    /**
    * @format email
    */
    email: string;
}

export interface SimpleForgotPassword {
    /**
    * @format email
    */
    email: string;
}

export interface UserProfile {
    /**
    * @label Email Address
    * @format email
    */
    email?: string;
    /**
    * @label First Name
    * @maxLength 150
    */
    firstName?: string;
    /**
    * @label Last Name
    * @maxLength 150
    */
    lastName?: string;
    /**
    * @label Phone Number
    * @maxLength 20
    */
    phoneNumber?: string | null;
    /**
    * @label Profile Picture
    */
    profilePicture?: File | null;
    /**
    * @label Date Joined
    * @format date-time
    */
    dateJoined?: string;
}

export interface UserProfileUpdate {
    /**
    * @label First Name
    * @maxLength 150
    */
    firstName?: string;
    /**
    * @label Last Name
    * @maxLength 150
    */
    lastName?: string;
    /**
    * @maxLength 20
    */
    phoneNumber?: string;
    /**
    * @label Profile Picture
    */
    profilePicture?: File | null;
}

export interface UserRegistration {
    /**
    * @label Email Address
    * @maxLength 254
    * @format email
    */
    email: string;
    password: string;
    passwordConfirm: string;
    /**
    * @label First Name
    * @maxLength 150
    * @default ""
    */
    firstName?: string;
    /**
    * @label Last Name
    * @maxLength 150
    * @default ""
    */
    lastName?: string;
    /**
    * @maxLength 20
    */
    phoneNumber: string;
}

export interface UserRegistrationSuccess {
    /**
    * @format email
    */
    email: string;
    message: string;
}

